"""
cli_anything MeerK40t bridge plugin.

This plugin back-fills three upstream fixes from MeerK40t pull request
#3249 into a stock PyPI install of meerk40t that has not yet received them.
It is registered through the ``meerk40t.extension`` entry point so that a
normal ``pip install`` of cli-anything makes the fixes available without any
manual configuration.

The three fixes are:

1. Console and web handover resolved at execution time. Stock meerk40t
   resolves the ``gui/handover`` routine once during registration, which
   breaks the console server when the handover is registered afterwards.
   The fixed code looks the routine up lazily each time a command runs.
2. ``set`` reconstructs typed values. Setting a ``Length`` (or other typed)
   attribute with ``set my_len 10mm`` used to store the bare string; the
   fixed code rebuilds the typed value.
3. ``set`` emits feedback. After a successful assignment the command echoes
   the new value, and on a missing attribute it reports that clearly.

Design notes
------------
* Detection is behavioural and source based. A version fast-path short
  circuits when the installed meerk40t is already at or beyond the fixed
  release; otherwise each patch inspects the live code and only acts when
  the fix is genuinely absent.
* Every monkeypatch is individually guarded. A failure in one patch is
  reported on a console channel line citing PR #3249 and never propagates
  into kernel boot.
* The plugin is idempotent. Re-running it (or loading it twice) applies
  each fix at most once and is safe when the fix is already present.
* When the upstream release already contains the fixes, the plugin does
  nothing. Frozen application builds that bundle a fixed meerk40t are
  therefore unaffected.
"""

from __future__ import annotations

import inspect
from meerk40t.kernel import _

# Upstream pull request that introduces these fixes permanently.
UPSTREAM_PR = "#3249"

# PR #3249 is not merged upstream yet; no released version carries the
# fixes. Once it ships, set this to that release to fast-path detection.
UPSTREAM_FIXED_VERSION = None

# Names of the three independent patches, used for status tracking.
PATCH_HANDOVER = "console-handover"
PATCH_TYPED = "typed-settings"
PATCH_FEEDBACK = "set-feedback"

_CHANNEL_NAME = "cli_anything_bridge"

# --- Source fragments used to back-fill the handover fix -------------------

_BROKEN_HANDOVER_BLOCK = """            handover = None
            for result in root.find("gui/handover"):
                # Do we have a thread handover routine?
                if result is not None:
                    handover, _path, suffix_path = result
                    break"""

_FIND_HANDOVER_DEF = """            def find_handover():
                return root.lookup("gui/handover")"""


# ---------------------------------------------------------------------------
# Version and status helpers
# ---------------------------------------------------------------------------

def _meerk40t_version():
    try:
        from importlib.metadata import version as _v

        return _v("meerk40t")
    except Exception:
        return None


def _version_tuple(value):
    parts = []
    for piece in str(value).split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            break
    return tuple(parts)


def _upstream_fixed():
    """True only when the installed meerk40t demonstrably has the fixes.

    Version fast path applies only once UPSTREAM_FIXED_VERSION names a real
    release. Otherwise detect behaviourally: the fixed console_server defines
    an execution-time find_handover, and the fixed set_command reports
    unknown attributes ("No such attribute").
    """
    if UPSTREAM_FIXED_VERSION is not None:
        installed = _meerk40t_version()
        if installed is not None:
            if _version_tuple(installed) >= UPSTREAM_FIXED_VERSION:
                return True
    try:
        from meerk40t.network import console_server

        src = inspect.getsource(console_server)
        if "find_handover" not in src:
            return False
        from meerk40t.kernel import kernel as kernel_mod

        return "No such attribute" in inspect.getsource(kernel_mod)
    except Exception:
        return False


def _patch_status(kernel):
    """Return the per-patch status dictionary stored on the kernel."""
    status = getattr(kernel, "_cli_anything_mk_patches", None)
    if status is None:
        status = {}
        try:
            kernel._cli_anything_mk_patches = status
        except Exception:
            pass
    return status


def _get_channel(kernel):
    try:
        return kernel.channel(_CHANNEL_NAME)
    except Exception:
        return None


def _emit_skip(channel, name, exc):
    if channel is None:
        return
    channel(
        f"cli_anything meerk40t bridge: skipped patch '{name}' (PR {UPSTREAM_PR}): {exc}"
    )


def _unwrap_source(func):
    """Return the source of a console command, unwrapping decorators."""
    current = func
    while hasattr(current, "__wrapped__"):
        current = current.__wrapped__
    return inspect.getsource(current)


# ---------------------------------------------------------------------------
# Patch 1: console and web server handover resolved at execution time
# ---------------------------------------------------------------------------

def _transform_console_server_source(src):
    """Back-fill the handover fix into a ``console_server.plugin`` source.

    Returns the patched source, or the original source unchanged when the
    fix is already present or the source is not recognised.
    """
    if "find_handover" in src:
        return src
    if _BROKEN_HANDOVER_BLOCK in src and "if handover is None:" in src:
        src = src.replace(_BROKEN_HANDOVER_BLOCK, _FIND_HANDOVER_DEF)
        src = src.replace(
            "                    if handover is None:",
            "                    handover = find_handover()\n                    if handover is None:",
        )
        return src
    return src


def _transform_web_server_send_command(src):
    """Back-fill the handover fix into a ``WebServer.send_command`` source."""
    if 'lookup("gui/handover")' in src:
        return src
    old = '        if self.handover is None:\n            self.context(f"{command}\\n")'
    new = (
        '        if self.handover is None:\n'
        '            self.handover = self.context.root.lookup("gui/handover")\n'
        '            self.context(f"{command}\\n")'
    )
    if old in src:
        return src.replace(old, new)
    return src


def _rebind_module_function(module, attr, transform, channel, status, name):
    """Re-execute a module-level function with a transformed source.

    Returns True when the source was actually changed and rebound, or False
    when the fix was already present (or the source was not recognised).
    """
    applied_marker = "_cli_anything_patched_" + attr
    if getattr(module, applied_marker, False):
        return False
    func = getattr(module, attr, None)
    if func is None:
        return False
    src = inspect.getsource(func)
    fixed = transform(src)
    if fixed == src:
        # Already fixed (or unrecognised). Mark so we do not retry.
        setattr(module, applied_marker, True)
        return False
    namespace = dict(getattr(module, "__dict__", {}))
    filename = getattr(module, "__file__", None) or f"<{module.__name__}.{attr}>"
    exec(compile(fixed, filename, "exec"), namespace)
    setattr(module, attr, namespace[attr])
    setattr(module, applied_marker, True)
    return True


def _patch_web_server(ws_module, channel, status, name):
    """Patch ``WebServer.send_command`` inside the web server module.

    Returns True when the method was actually changed, or False otherwise.
    """
    applied_marker = "_cli_anything_patched_WebServer"
    if getattr(ws_module, applied_marker, False):
        return False
    web_server = getattr(ws_module, "WebServer", None)
    if web_server is None:
        return False
    method = getattr(web_server, "send_command", None)
    if method is None:
        return False
    src = inspect.getsource(method)
    fixed = _transform_web_server_send_command(src)
    if fixed == src:
        setattr(ws_module, applied_marker, True)
        return False
    namespace = {}
    exec(compile(fixed, "<web_server.send_command>", "exec"), namespace)
    web_server.send_command = namespace["send_command"]
    setattr(ws_module, applied_marker, True)
    return True




def _patch_network_handover(kernel, channel):
    name = PATCH_HANDOVER
    status = _patch_status(kernel)
    if status.get(name) == "applied":
        return
    try:
        import meerk40t.network.console_server as console_server

        console_changed = _rebind_module_function(
            console_server,
            "plugin",
            _transform_console_server_source,
            channel,
            status,
            name,
        )
        import meerk40t.network.web_server as web_server

        web_changed = _patch_web_server(web_server, channel, status, name)
        # Mark applied only when at least one module was actually changed.
        if console_changed or web_changed:
            status[name] = "applied"
        else:
            status[name] = "skipped-already-fixed"
    except Exception as exc:
        status[name] = "failed"
        _emit_skip(channel, name, exc)


# ---------------------------------------------------------------------------
# Patches 2 and 3: typed values and feedback for the ``set`` command
# ---------------------------------------------------------------------------

def _get_registered_set(kernel):
    for funct, _name, _regex in kernel.find("command", "None", "set"):
        return funct
    return None


def _set_source_markers(src):
    has_typed = "type(v)(value)" in src
    has_feedback = "No such attribute" in src
    return has_typed, has_feedback


def _register_fixed_set(kernel):
    """Register a faithful copy of the fixed upstream ``set`` command."""

    def set_command(channel, _, path=None, args=tuple(), **kwargs):
        relevant_context = (
            kernel.get_context(path) if path is not None else kernel.root
        )
        if len(args) == 0:
            for attr in dir(relevant_context):
                v = getattr(relevant_context, attr)
                if attr.startswith("_") or not isinstance(v, (int, float, str, bool)):
                    continue
                channel(f'"{attr}" := {str(v)}')
            return
        if len(args) >= 2:
            attr = args[0]
            value = args[1]
            try:
                if hasattr(relevant_context, attr):
                    v = getattr(relevant_context, attr)
                    if isinstance(v, bool):
                        if value == "False" or value == "false" or value == 0:
                            setattr(relevant_context, attr, False)
                        else:
                            setattr(relevant_context, attr, True)
                    elif isinstance(v, int):
                        setattr(relevant_context, attr, int(value))
                    elif isinstance(v, float):
                        setattr(relevant_context, attr, float(value))
                    elif isinstance(v, str):
                        setattr(relevant_context, attr, str(value))
                    else:
                        # Typed settings (e.g. Length, Angle): reconstruct the
                        # typed value from the string form and keep it typed on
                        # the live context.
                        setattr(relevant_context, attr, type(v)(value))
                    channel(
                        f'"{attr}" := {str(getattr(relevant_context, attr))}'
                    )
                else:
                    channel(_("No such attribute: {attr}").format(attr=attr))
            except RuntimeError:
                channel(_("Attempt failed. Produced a runtime error."))
            except ValueError:
                channel(_("Attempt failed. Produced a value error."))
            except AttributeError:
                channel(_("Attempt failed. Produced an attribute error."))
            except TypeError:
                channel(_("Attempt failed. Produced a type error."))
        return

    try:
        kernel.console_command_remove("set")
    except Exception:
        pass
    # Replicate the upstream decorator stack (bottom-up): the command
    # registration first, then the -p path option annotation on top.
    registered = kernel.console_command(
        "set", help="set [<key> <value>] : " + _("set or list variables")
    )(set_command)
    kernel.console_option(
        "path", "p", type=str, help=_("Path of variable to set.")
    )(registered)


def _patch_set_typed(kernel, channel):
    name = PATCH_TYPED
    status = _patch_status(kernel)
    if status.get(name) == "applied":
        return
    try:
        func = _get_registered_set(kernel)
        if func is None:
            return
        has_typed, _has_feedback = _set_source_markers(_unwrap_source(func))
        if has_typed:
            status[name] = "skipped-already-fixed"
            return
        _register_fixed_set(kernel)
        status[name] = "applied"
    except Exception as exc:
        status[name] = "failed"
        _emit_skip(channel, name, exc)


def _patch_set_feedback(kernel, channel):
    name = PATCH_FEEDBACK
    status = _patch_status(kernel)
    if status.get(name) == "applied":
        return
    try:
        func = _get_registered_set(kernel)
        if func is None:
            return
        _has_typed, has_feedback = _set_source_markers(_unwrap_source(func))
        if has_feedback:
            status[name] = "skipped-already-fixed"
            return
        _register_fixed_set(kernel)
        status[name] = "applied"
    except Exception as exc:
        status[name] = "failed"
        _emit_skip(channel, name, exc)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def apply_backfill_patches(kernel):
    """Apply all three upstream fixes that are missing from the live kernel.

    Safe to call multiple times and safe when the fixes are already present.
    Each patch is independently guarded so a single failure cannot stop the
    others or raise into kernel boot.
    """
    channel = _get_channel(kernel)
    if _upstream_fixed():
        status = _patch_status(kernel)
        for name in (PATCH_HANDOVER, PATCH_TYPED, PATCH_FEEDBACK):
            status[name] = "skipped-already-fixed"
        return
    _patch_network_handover(kernel, channel)
    _patch_set_typed(kernel, channel)
    _patch_set_feedback(kernel, channel)


def plugin(kernel, lifecycle=None):
    """MeerK40t extension entry point.

    Runs the back-fill at the early lifecycles, before the console or web
    servers can start. Never raises into kernel boot.
    """
    try:
        kernel._cli_anything_mk_loaded = True
        if lifecycle in ("preregister", "register", "boot", "postboot", "start"):
            apply_backfill_patches(kernel)
    except Exception:
        # A failure here must never break kernel boot.
        pass
    return None
