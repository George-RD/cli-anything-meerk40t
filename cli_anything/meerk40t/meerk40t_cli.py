"""cli-anything-meerk40t — Click CLI + REPL for the MeerK40t kernel."""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import os
import base64
import sys
from pathlib import Path
from typing import Any

import click

from cli_anything.meerk40t.core import device as device_mod
from cli_anything.meerk40t.core import elements as elements_mod
from cli_anything.meerk40t.core import export as export_mod
from cli_anything.meerk40t.core import operations as operations_mod
from cli_anything.meerk40t.core import project as project_mod
from cli_anything.meerk40t.core import session as session_mod
from cli_anything.meerk40t.utils.meerk40t_backend import (
    BackendError,
    Meerk40tBackend,
)
from cli_anything.meerk40t.utils import profiles as profiles_mod
from cli_anything.meerk40t.utils import submit as submit_mod
from cli_anything.meerk40t.utils import attach_client as attach_client_mod
from cli_anything.meerk40t.utils import job_prep as job_prep_mod
from cli_anything.meerk40t.utils import materials as materials_mod
from cli_anything.meerk40t.utils.repl_skin import ReplSkin
# Driver choices for --device. Extracted to a module-level var so the
# skill_generator regex (which chokes on nested parens in decorators) does
# not trip over click.Choice([...]).
DEVICE_CHOICES = click.Choice(
    ["dummy", "grbl", "lihuiyu", "moshi", "ruida", "newly", "balor"]
)
# Provenance / role enums extracted to module-level vars so the
# skill_generator regex (which rejects nested parens in Click decorators)
# does not trip over click.Choice([...]) inline.
PROVENANCE_CHOICES = click.Choice(["tested", "estimated"])
ROLE_CHOICES = click.Choice(["cut", "score", "etch"])


# Real stdout handle used for all user-facing output (kernel noise is suppressed).
_REAL_STDOUT = sys.stdout


class _KernelSuppressor:
    """Stdout wrapper that drops writes originating from the MeerK40t kernel.

    Kernel output reaches Python via ``print`` from inside the ``meerk40t``
    package. Writes from our own modules or from Click/REPL skin are allowed
    through to the original stdout.
    """

    def __init__(self, real):
        self._real = real

    def _kernel_frame(self):
        import sys as _sys
        try:
            i = 1
            while True:
                frame = _sys._getframe(i)
                mod = frame.f_globals.get("__name__", "")
                if mod.startswith("meerk40t"):
                    return True
                i += 1
        except ValueError:
            return False

    def write(self, s):
        if self._kernel_frame():
            return len(s)
        return self._real.write(s)

    def flush(self):
        self._real.flush()

    def isatty(self):
        return self._real.isatty()

    def fileno(self):
        return self._real.fileno()

    @property
    def encoding(self):
        return self._real.encoding

    @property
    def errors(self):
        return self._real.errors


class MeerkGroup(click.Group):
    """Custom Click group that shuts down the MeerK40t backend and restores stdout."""

    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        finally:
            sys.stdout = _REAL_STDOUT
            backend = ctx.obj.get("backend") if ctx.obj else None
            if backend is not None:
                backend.shutdown()


# ── Result formatting ───────────────────────────────────────────────────────


def _emit_human(data: Any) -> None:
    """Print a result in a human-readable form."""
    if isinstance(data, dict):
        if not data:
            click.echo("No results.")
            return
        for key, value in data.items():
            click.echo(f"{key}: {value}")
    elif isinstance(data, list):
        if not data:
            click.echo("No results.")
            return
        if data and isinstance(data[0], dict):
            headers = list(data[0].keys())
            rows = [[str(item.get(h, "")) for h in headers] for item in data]
            widths = [max(len(str(h)), max(len(row[i]) for row in rows)) for i, h in enumerate(headers)]
            header_line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
            click.echo(header_line)
            click.echo("-" * len(header_line))
            for row in rows:
                click.echo(" | ".join(row[i].ljust(widths[i]) for i in range(len(headers))))
        else:
            for item in data:
                click.echo(str(item))
    else:
        click.echo(str(data))


def _emit_now(ctx: click.Context, data: Any) -> None:
    """Emit a result immediately as JSON or human-readable text (--json)."""
    if ctx.obj.get("json"):
        click.echo(json.dumps(data, indent=2, default=str))
    else:
        _emit_human(data)


def _emit(ctx: click.Context, data: Any) -> None:
    """Emit a result, buffering it while inside a mutating command or REPL line.

    Buffering lets the completion boundary emit exactly once, only after persistence
    is proven, and discard the output if the command ultimately fails. A buffered
    failure payload marks the command as failed so the boundary can classify it.
    """
    buffer = ctx.obj.get("_capture")
    if buffer is not None:
        if isinstance(data, dict) and (data.get("error") is not None or data.get("ok") is False):
            ctx.obj["_capture_failed"] = True
        buffer.append(data)
        return
    _emit_now(ctx, data)


def _begin_capture(ctx: click.Context) -> None:
    ctx.obj["_capture"] = []
    ctx.obj["_capture_failed"] = False


def _flush_capture(ctx: click.Context) -> bool:
    """Emit any buffered output exactly once, then clear the buffer.

    Returns True if the buffer was non-empty (so callers avoid re-emitting the
    returned result).
    """
    buffer = ctx.obj.get("_capture")
    if not buffer:
        ctx.obj["_capture"] = None
        return False
    ctx.obj["_capture"] = None
    for item in buffer:
        _emit_now(ctx, item)
    return True


def _discard_capture(ctx: click.Context) -> None:
    ctx.obj["_capture"] = None


def _repl_exit(ctx: click.Context, code: int, repl: bool) -> int:
    if repl:
        return code
    ctx.exit(code)


def _autosave_once(ctx: click.Context) -> None:
    """Persist the active session/project after a successful mutating command.

    Raises BackendError (or any persistence exception) so the completion boundary
    can convert it into a single structured failure. Never swallows errors.
    """
    if ctx.obj.get("dry_run"):
        return
    backend = ctx.obj.get("backend")
    sess = ctx.obj.get("session")
    project_path = ctx.obj.get("project_path")
    if not sess and project_path:
        backend.save_svg(project_path)
    elif sess and getattr(sess, "svg_path", None):
        sess.save(backend)


def _complete_command(ctx: click.Context, result: Any, *, mutating: bool = False,
                      repl: bool = False, on_success=None) -> int:
    """Single completion boundary for every command and REPL line.

    Classifies ``result`` (or any buffered failure payload) as success/failure,
    auto-saves exactly once after a proven successful mutation, fires
    ``on_success`` only once persistence is confirmed, flushes buffered output
    exactly once, and sets the process exit code. In REPL mode it returns the
    code instead of calling ``ctx.exit`` so the loop can continue.
    """
    is_failure = (
        isinstance(result, dict)
        and (result.get("error") is not None or result.get("ok") is False)
    ) or ctx.obj.get("_capture_failed")
    if is_failure:
        # Command-level failure: show output the command already produced, then
        # emit the error only if nothing was buffered.
        gate = isinstance(result, dict) and result.get("acknowledgeable_gate")
        code = 2 if gate else 1
        if not _flush_capture(ctx):
            _emit_now(ctx, result)
        return _repl_exit(ctx, code, repl)
    if mutating and not ctx.obj.get("dry_run"):
        try:
            _autosave_once(ctx)
        except BackendError as exc:
            # Persistence failed AFTER a successful command: discard the buffered
            # success output and report only the persistence error.
            _discard_capture(ctx)
            _emit_now(
                ctx,
                {"error": str(exc), "path": exc.path, "category": exc.category},
            )
            return _repl_exit(ctx, 1, repl)
        except Exception as exc:  # noqa: BLE001 - convert any persistence fault
            _discard_capture(ctx)
            _emit_now(
                ctx,
                {"error": f"autosave failed: {exc}", "category": "infrastructure"},
            )
            return _repl_exit(ctx, 1, repl)
    if callable(on_success):
        on_success()
    # Proven success: emit buffered output exactly once. If the command
    # returned a result dict without emitting (e.g. REPL branches that
    # assign ``result = core(...)``), emit that once.
    if not _flush_capture(ctx) and result is not None:
        _emit_now(ctx, result)
    return _repl_exit(ctx, 0, repl)


def mutating(f):
    """Decorator that routes a mutating command through the completion boundary.

    The command emits via ``_emit`` (buffered); the boundary flushes that output
    exactly once after autosave succeeds, converts any uncaught exception into a
    single failure payload, and sets the exit code. Commands never call
    ``ctx.exit`` and never emit on failure themselves.
    """
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        ctx = click.get_current_context()
        _begin_capture(ctx)
        try:
            result = f(*args, **kwargs)
        except BackendError as exc:
            return _complete_command(
                ctx,
                {"error": str(exc), "path": exc.path, "category": exc.category},
                mutating=True,
            )
        except Exception as exc:  # noqa: BLE001 - convert any command fault once
            return _complete_command(
                ctx,
                {"error": str(exc) or exc.__class__.__name__, "category": "infrastructure"},
                mutating=True,
            )
        return _complete_command(ctx, result, mutating=True)

    wrapper._routes_outcome = True
    return wrapper


class OutcomeCommand(click.Command):
    """Route every CLI command through the completion boundary so a structured
    failure yields a nonzero exit code and output is emitted exactly once.

    Commands already wrapped by ``@mutating`` route themselves (marked
    ``_routes_outcome``) and are invoked directly. Commands that manage their
    own exit via ``ctx.exit`` raise ``click.exceptions.Exit``; their buffered
    output is flushed and the chosen exit code is preserved.
    """

    def invoke(self, ctx: click.Context):
        callback = self.callback
        if getattr(callback, "_routes_outcome", False):
            return super().invoke(ctx)
        _begin_capture(ctx)
        try:
            result = super().invoke(ctx)
        except (click.exceptions.Exit, click.exceptions.Abort):
            _flush_capture(ctx)
            raise
        except BackendError as exc:
            return _complete_command(
                ctx,
                {"error": str(exc), "path": exc.path, "category": exc.category},
            )
        except Exception as exc:  # noqa: BLE001 - convert any command fault once
            return _complete_command(
                ctx,
                {"error": str(exc) or exc.__class__.__name__, "category": "infrastructure"},
            )
        return _complete_command(ctx, result)


class OutcomeGroup(click.Group):
    """Subgroup type whose commands and nested groups route through the boundary."""

    command_class = OutcomeCommand
    group_class = "OutcomeGroup"


OutcomeGroup.group_class = OutcomeGroup
MeerkGroup.command_class = OutcomeCommand
MeerkGroup.group_class = OutcomeGroup


# ── Main CLI group ───────────────────────────────────────────────────────────
def _apply_machine_profile(backend, profile: dict) -> None:
    """Apply a machine profile's bed dimensions to the active device view.

    Profiles carry ``bedwidth``/``bedheight`` as strings like ``"410mm"``.
    Assigning them on the active device service (NOT a root-context ``set``,
    which targets the kernel root context and never reaches the device) and
    triggering ``realize()`` updates the coordinate view that export placement
    maths runs against, so plan coordinates and the Y-flip use the real bed.
    """
    dev = backend.device()
    if dev is None:
        return
    for attr in ("bedwidth", "bedheight"):
        value = profile.get(attr)
        if value:
            setattr(dev, attr, value)
    realize = getattr(dev, "realize", None)
    if callable(realize):
        try:
            realize()
        except Exception:
            pass

@click.group(cls=MeerkGroup, invoke_without_command=True)
@click.option("--json/--no-json", "json_mode", default=False, help="Output results as JSON.")
@click.option("--project", "-p", "project_path", default=None, help="Project SVG file to open.")
@click.option("--session", "-s", "session_path", default=None, help="Session JSON file.")
@click.option("--dry-run", is_flag=True, default=False, help="Do not auto-save after mutations.")
@click.option("--device", "device", type=DEVICE_CHOICES, default="dummy", show_default=True, help="Device driver to activate (dummy, grbl, lihuiyu, moshi, ruida, newly, balor).")
@click.option("--port", "port", default=None, help="Serial port for the device, e.g. /dev/cu.usbserial-10.")
@click.option("--machine", "machine", default=None, help="Load a machine profile by name (e.g. sculpfun-s9). The port is only required for serial commands (connect, check, jog, goto, frame, setup); offline commands (export, elements, machine list, project ops) work without one.")
@click.option("--baud", "baud", default=115200, show_default=True, help="Baud rate for the serial device.")
@click.pass_context
def cli(
    ctx: click.Context,
    json_mode: bool,
    project_path: str | None,
    session_path: str | None,
    dry_run: bool,
    device: str,
    port: str | None,
    baud: int,
    machine: str | None,
):
    ctx.ensure_object(dict)
    sys.stdout = _KernelSuppressor(_REAL_STDOUT)
    ctx.obj["json"] = json_mode
    ctx.obj["dry_run"] = dry_run
    ctx.obj["project_path"] = project_path

    # Resolve --machine profile BEFORE starting the backend; an unknown name
    # is a clean JSON error and must not start the kernel.
    profile = None
    if machine is not None:
        try:
            profile = profiles_mod.load_profile(machine)
        except ValueError:
            profile = None
        if profile is None:
            _emit(
                ctx,
                {
                    "error": f"unknown machine profile: {machine!r}",
                    "known": profiles_mod.available_names(),
                },
            )
            sys.stdout = _REAL_STDOUT
            ctx.exit(1)
        if profile.get("device"):
            device = profile["device"]
        if profile.get("baud"):
            baud = profile["baud"]

    # The attach subcommand is a thin client over the consoleserver control
    # channel and never touches the local kernel; skip the backend bootstrap
    # so `attach status` against a missing server fails fast instead of
    # paying the Meerk40t kernel startup cost.
    if ctx.invoked_subcommand == "attach":
        backend = None
    else:
        backend = Meerk40tBackend(device=device, port=port, baud=baud)
        backend.start()
    ctx.obj["backend"] = backend

    # Apply the profile's bed dimensions to the active device view.
    if profile is not None and backend is not None:
        _apply_machine_profile(backend, profile)
    ctx.obj["machine_name"] = machine
    ctx.obj["project_path"] = project_path
    if session_path:
        try:
            sess = session_mod.Session(session_path)
        except BackendError as exc:
            _emit(
                ctx,
                {"error": str(exc), "path": exc.path, "category": exc.category},
            )
            sys.stdout = _REAL_STDOUT
            ctx.exit(1)
    else:
        sess = None
    ctx.obj["session"] = sess

    # Deterministic precedence: an explicit --project wins over --session alone.
    if project_path and backend is not None:
        result = project_mod.open_project(backend, project_path)
        if result.get("error") is not None:
            _emit(ctx, result)
            sys.stdout = _REAL_STDOUT
            ctx.exit(1)
        if sess:
            sess.name = os.path.basename(project_path)
            sess.svg_path = project_path
    elif sess and getattr(sess, "svg_path", None) and backend is not None:
        # --session alone restores the recorded SVG.
        result = project_mod.open_project(backend, sess.svg_path)
        if result.get("error") is not None:
            _emit(ctx, result)
            sys.stdout = _REAL_STDOUT
            ctx.exit(1)

    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


# ── Project commands ────────────────────────────────────────────────────────


@cli.group()
def project():
    """Project management (SVG files)."""


@project.command("new")
@click.option("--name", default="Untitled", help="Project name.")
@click.pass_context
@mutating
def project_new(ctx: click.Context, name: str):
    """Create a new project."""
    result = project_mod.create_project(ctx.obj["backend"], name=name)
    sess = ctx.obj.get("session")
    if sess:
        sess.name = name
        sess.modified = True
    _emit(ctx, result)


@project.command("open")
@click.argument("path")
@click.pass_context
@mutating
def project_open(ctx: click.Context, path: str):
    """Open an existing SVG project."""
    result = project_mod.open_project(ctx.obj["backend"], path)
    # Only rebind the active project on success: a failed open must never leave
    # the previous project's binding in place (which would autosave B into A).
    if result.get("error") is None:
        ctx.obj["project_path"] = path
        sess = ctx.obj.get("session")
        if sess:
            sess.name = os.path.basename(path)
            sess.svg_path = path
    _emit(ctx, result)


@project.command("save")
@click.argument("path")
@click.option("--version", default="default", help="SVG version: default, plain, compressed.")
@click.pass_context
@mutating
def project_save(ctx: click.Context, path: str, version: str):
    """Save the current project to an SVG file."""
    result = project_mod.save_project(ctx.obj["backend"], path, version=version)
    if result.get("error") is None:
        ctx.obj["project_path"] = path
        sess = ctx.obj.get("session")
        if sess:
            sess.name = os.path.basename(path)
            sess.svg_path = path
            sess.modified = False
    _emit(ctx, result)


@project.command("info")
@click.pass_context
def project_info_cmd(ctx: click.Context):
    """Show project information."""
    _emit(ctx, project_mod.project_info(ctx.obj["backend"]))


@project.command("close")
@click.pass_context
@mutating
def project_close(ctx: click.Context):
    """Close the current project."""
    result = project_mod.close_project(ctx.obj["backend"])
    if result.get("error") is None:
        # Drop the binding so autosave writes no stale project SVG.
        ctx.obj["project_path"] = None
        sess = ctx.obj.get("session")
        if sess:
            sess.svg_path = None
            sess.modified = False
    _emit(ctx, result)


# ── Element commands ──────────────────────────────────────────────────────────


@cli.group()
def elements():
    """Element operations."""


@elements.command("circle")
@click.argument("cx")
@click.argument("cy")
@click.argument("r")
@click.option("--stroke", default=None, help="Stroke color.")
@click.option("--fill", default=None, help="Fill color.")
@click.pass_context
@mutating
def elements_circle(ctx: click.Context, cx: str, cy: str, r: str, stroke: str | None, fill: str | None):
    """Add a circle element."""
    _emit(ctx, elements_mod.add_circle(ctx.obj["backend"], cx, cy, r, stroke=stroke, fill=fill))


@elements.command("rect")
@click.argument("x")
@click.argument("y")
@click.argument("w")
@click.argument("h")
@click.option("--stroke", default=None, help="Stroke color.")
@click.option("--fill", default=None, help="Fill color.")
@click.pass_context
@mutating
def elements_rect(ctx: click.Context, x: str, y: str, w: str, h: str, stroke: str | None, fill: str | None):
    """Add a rectangle element."""
    _emit(ctx, elements_mod.add_rect(ctx.obj["backend"], x, y, w, h, stroke=stroke, fill=fill))


@elements.command("ellipse")
@click.argument("cx")
@click.argument("cy")
@click.argument("rx")
@click.argument("ry")
@click.pass_context
@mutating
def elements_ellipse(ctx: click.Context, cx: str, cy: str, rx: str, ry: str):
    """Add an ellipse element."""
    _emit(ctx, elements_mod.add_ellipse(ctx.obj["backend"], cx, cy, rx, ry))


@elements.command("line")
@click.argument("x1")
@click.argument("y1")
@click.argument("x2")
@click.argument("y2")
@click.pass_context
@mutating
def elements_line(ctx: click.Context, x1: str, y1: str, x2: str, y2: str):
    """Add a line element."""
    _emit(ctx, elements_mod.add_line(ctx.obj["backend"], x1, y1, x2, y2))


@elements.command("polyline")
@click.argument("coords", nargs=-1)
@click.pass_context
@mutating
def elements_polyline(ctx: click.Context, coords: tuple[str, ...]):
    """Add a polyline element (pairs of coordinates)."""
    if len(coords) % 2 != 0:
        raise click.UsageError("Polyline requires an even number of coordinates.")
    points = [(coords[i], coords[i + 1]) for i in range(0, len(coords), 2)]
    _emit(ctx, elements_mod.add_polyline(ctx.obj["backend"], points))


@elements.command("text")
@click.argument("x")
@click.argument("y")
@click.argument("text")
@click.pass_context
@mutating
def elements_text(ctx: click.Context, x: str, y: str, text: str):
    """Add a text element."""
    _emit(ctx, elements_mod.add_text(ctx.obj["backend"], x, y, text))


@elements.command("list")
@click.pass_context
def elements_list(ctx: click.Context):
    """List elements in the project."""
    _emit(ctx, elements_mod.list_elements(ctx.obj["backend"]))


@elements.command("delete")
@click.argument("index", type=int)
@click.pass_context
@mutating
def elements_delete(ctx: click.Context, index: int):
    """Delete an element by index."""
    _emit(ctx, elements_mod.delete_element(ctx.obj["backend"], index))


@elements.command("select")
@click.argument("index", type=int)
@click.pass_context
@mutating
def elements_select(ctx: click.Context, index: int):
    """Select an element by index."""
    _emit(ctx, elements_mod.select_element(ctx.obj["backend"], index))


@elements.command("clear")
@click.pass_context
@mutating
def elements_clear(ctx: click.Context):
    """Clear all elements."""
    _emit(ctx, elements_mod.clear_elements(ctx.obj["backend"]))


@elements.command("frame")
@click.pass_context
@mutating
def elements_frame(ctx: click.Context):
    """Add a frame element."""
    _emit(ctx, elements_mod.frame(ctx.obj["backend"]))


@elements.command("translate")
@click.argument("index", type=int)
@click.argument("tx")
@click.argument("ty")
@click.option("--absolute", "-a", is_flag=True, help="Translate to absolute coordinates.")
@click.pass_context
@mutating
def elements_translate(ctx: click.Context, index: int, tx: str, ty: str, absolute: bool):
    """Translate an element."""
    _emit(ctx, elements_mod.translate_element(ctx.obj["backend"], index, tx, ty, absolute=absolute))


@elements.command("scale")
@click.argument("index", type=int)
@click.argument("scale_x")
@click.argument("scale_y", required=False, default=None)
@click.option("--absolute", "-a", is_flag=True, help="Scale to absolute size.")
@click.option("--px", "-x", default=None, help="Origin X coordinate for scaling.")
@click.option("--py", "-y", default=None, help="Origin Y coordinate for scaling.")
@click.pass_context
@mutating
def elements_scale(ctx: click.Context, index: int, scale_x: str, scale_y: str | None, absolute: bool, px: str | None, py: str | None):
    """Scale an element."""
    _emit(ctx, elements_mod.scale_element(ctx.obj["backend"], index, scale_x, scale_y, absolute=absolute, px=px, py=py))


@elements.command("rotate")
@click.argument("index", type=int)
@click.argument("angle")
@click.option("--absolute", "-a", is_flag=True, help="Rotate to absolute angle.")
@click.option("--cx", "-x", default=None, help="Rotation center X.")
@click.option("--cy", "-y", default=None, help="Rotation center Y.")
@click.pass_context
@mutating
def elements_rotate(ctx: click.Context, index: int, angle: str, absolute: bool, cx: str | None, cy: str | None):
    """Rotate an element."""
    _emit(ctx, elements_mod.rotate_element(ctx.obj["backend"], index, angle, absolute=absolute, cx=cx, cy=cy))


ALIGN_CHOICES = click.Choice(["left", "right", "top", "bottom", "center", "centerh", "centerv"])

@elements.command("align")
@click.argument("mode", type=ALIGN_CHOICES)
@click.option("--index", "-i", "indexes", type=int, multiple=True, help="Element index to align.")
@click.pass_context
@mutating
def elements_align(ctx: click.Context, mode: str, indexes: tuple[int, ...]):
    """Align elements."""
    _emit(ctx, elements_mod.align_elements(ctx.obj["backend"], mode, indexes=list(indexes) if indexes else None))


@elements.command("group")
@click.option("--label", "-l", default=None, help="Optional label for the group.")
@click.option("--index", "-i", "indexes", type=int, multiple=True, help="Element index to group.")
@click.pass_context
@mutating
def elements_group(ctx: click.Context, label: str | None, indexes: tuple[int, ...]):
    """Group elements."""
    _emit(ctx, elements_mod.group_elements(ctx.obj["backend"], label, indexes=list(indexes) if indexes else None))


@elements.command("ungroup")
@click.option("--index", "-i", type=int, default=None, help="Index of group/file node to ungroup.")
@click.pass_context
@mutating
def elements_ungroup(ctx: click.Context, index: int | None):
    """Ungroup elements."""
    _emit(ctx, elements_mod.ungroup_elements(ctx.obj["backend"], index=index))


# ── Operation commands ────────────────────────────────────────────────────────


@cli.group()
def operations():
    """Operation management."""


@operations.command("list")
@click.pass_context
def operations_list(ctx: click.Context):
    """List operations."""
    _emit(ctx, operations_mod.list_operations(ctx.obj["backend"]))


@operations.command("add")
@click.argument("op_type")
@click.pass_context
@mutating
def operations_add(ctx: click.Context, op_type: str):
    """Add an operation (cut, engrave, raster, image, dots)."""
    _emit(ctx, operations_mod.add_operation(ctx.obj["backend"], op_type))


@operations.command("classify")
@click.pass_context
@mutating
def operations_classify(ctx: click.Context):
    """Classify elements into operations."""
    _emit(ctx, operations_mod.classify_elements(ctx.obj["backend"]))


@operations.command("declassify")
@click.pass_context
@mutating
def operations_declassify(ctx: click.Context):
    """Declassify elements from operations."""
    _emit(ctx, operations_mod.declassify_elements(ctx.obj["backend"]))


@operations.command("set")
@click.argument("index", type=int)
@click.argument("key")
@click.argument("value")
@click.pass_context
@mutating
def operations_set(ctx: click.Context, index: int, key: str, value: str):
    """Set an operation property."""
    _emit(ctx, operations_mod.set_operation(ctx.obj["backend"], index, key, value))


@operations.command("delete")
@click.argument("index", type=int)
@click.pass_context
@mutating
def operations_delete(ctx: click.Context, index: int):
    """Delete an operation by index."""
    _emit(ctx, operations_mod.delete_operation(ctx.obj["backend"], index))


@operations.command("clear")
@click.pass_context
@mutating
def operations_clear(ctx: click.Context):
    """Clear all operations."""
    _emit(ctx, operations_mod.clear_operations(ctx.obj["backend"]))


# ── Device commands ───────────────────────────────────────────────────────────


@cli.group()
def device():
    """Device control."""


@device.command("list")
@click.pass_context
def device_list(ctx: click.Context):
    """List devices."""
    _emit(ctx, device_mod.list_devices(ctx.obj["backend"]))


@device.command("status")
@click.pass_context
def device_status(ctx: click.Context):
    """Show device status."""
    _emit(ctx, device_mod.device_status(ctx.obj["backend"]))


@device.command("home")
@click.pass_context
@mutating
def device_home(ctx: click.Context):
    """Home the device."""
    _emit(ctx, device_mod.home(ctx.obj["backend"]))


@device.command("physical-home")
@click.pass_context
@mutating
def device_physical_home(ctx: click.Context):
    """Perform physical home."""
    _emit(ctx, device_mod.physical_home(ctx.obj["backend"]))


@device.command("detect")
@click.option("--probe", is_flag=True, default=False, help="Probe each candidate port for GRBL firmware.")
@click.pass_context
def device_detect(ctx, probe):
    """List candidate serial ports (optionally probe each for GRBL)."""
    _emit(ctx, device_mod.detect(probe=probe))


@device.command("check")
@click.pass_context
def device_check(ctx):
    """Preflight the GRBL device: read $N/$$ and verify the configuration."""
    _emit(ctx, device_mod.check(ctx.obj["backend"]))


@device.command("jog")
@click.argument("dx")
@click.argument("dy")
@click.option("--feed", default=600, type=int, help="Feed rate in mm/min.")
@click.pass_context
@mutating
def device_jog(ctx, dx, dy, feed):
    """Relative jog in machine mm (origin front-left, +Y away from operator)."""
    _emit(ctx, device_mod.jog(ctx.obj["backend"], float(dx), float(dy), feed=feed))


@device.command("goto")
@click.argument("x")
@click.argument("y")
@click.option("--feed", default=3000, type=int, help="Feed rate in mm/min.")
@click.pass_context
@mutating
def device_goto(ctx, x, y, feed):
    """Absolute jog in machine mm (origin front-left, +Y away from operator)."""
    _emit(ctx, device_mod.goto(ctx.obj["backend"], float(x), float(y), feed=feed))


@device.command("frame")
@click.argument("x")
@click.argument("y")
@click.argument("w")
@click.argument("h")
@click.option("--feed", default=1500, type=int, help="Feed rate in mm/min.")
@click.pass_context
@mutating
def device_frame(ctx, x, y, w, h, feed):
    """Dry-frame a rectangle in machine mm (origin front-left, +Y away)."""
    _emit(ctx, device_mod.frame(ctx.obj["backend"], float(x), float(y), float(w), float(h), feed=feed))


@device.command("setup")
@click.option("--save-profile", "save_profile", default=None, help="Save a user machine profile with this NAME from the live readback.")
@click.pass_context
@mutating
def device_setup(ctx, save_profile):
    """Capture the live device configuration into a user machine profile."""
    if not save_profile:
        _emit(ctx, {"error": "device setup requires --save-profile NAME", "pass": False})
        return
    _emit(ctx, device_mod.setup_profile(ctx.obj["backend"], save_profile))


@device.command("machines")
@click.pass_context
def device_machines(ctx):
    """List available machine profiles (alias of `machine list`)."""
    _emit(ctx, {"profiles": profiles_mod.list_profiles()})


@cli.group()
def machine():
    """Machine profile management."""


@machine.command("list")
@click.pass_context
def machine_list(ctx):
    """List available machine profiles (bundled and user)."""
    _emit(ctx, {"profiles": profiles_mod.list_profiles()})
@cli.group()
def profile():
    """Community machine-profile submission."""


@profile.command("submit")
@click.argument("name")
@click.option("--yes", is_flag=True, default=False, help="Actually submit the profile. Without this flag the command prints the plan and exits; nothing is sent.")
@click.pass_context
def profile_submit(ctx, name, yes):
    """Submit a machine profile to the community collection.

    Loads the named profile, validates it against the community schema, then
    either opens a pull request via the gh CLI (when installed, authenticated,
    and --yes is given) or prints the profile JSON and a pre-filled new-issue
    URL. Nothing is submitted without --yes; without it the command prints
    what would be sent and the exact command to confirm, leaving consent
    with the human.
    """
    result = submit_mod.submit_profile(name, yes=yes)
    if not result.get("ok"):
        _emit(ctx, result)
        sys.stdout = _REAL_STDOUT
        ctx.exit(1)
    if ctx.obj.get("json"):
        _emit(ctx, result)
        return
    click.echo(f"Profile: {result['name']}")
    click.echo(f"Target file: {result['community_file']}")
    click.echo("")
    click.echo("Profile JSON:")
    click.echo(json.dumps(result["profile"], indent=2))
    click.echo("")
    click.echo(f"Pre-filled issue URL:\n{result['issue_url']}")
    if result.get("confirm_command"):
        click.echo("")
        click.echo("To submit via a pull request, run:")
        click.echo(f"  {result['confirm_command']}")
    else:
        click.echo("")
        click.echo("Install the gh CLI and re-run with --yes to open a PR,")
        click.echo("or open the issue URL above to submit manually.")
@device.command("move")
@click.argument("x")
@click.argument("y")
@click.option("--absolute/--relative", "absolute", default=True, help="Move absolute or relative.")
@click.pass_context
@mutating
def device_move(ctx: click.Context, x: str, y: str, absolute: bool):
    """Move the device."""
    _emit(ctx, device_mod.move(ctx.obj["backend"], x, y, absolute=absolute))


@device.command("info")
@click.pass_context
def device_info_cmd(ctx: click.Context):
    """Show device information."""
    _emit(ctx, device_mod.device_info(ctx.obj["backend"]))


@device.command("connect")
@click.pass_context
def device_connect(ctx: click.Context):
    """Open the active device's serial connection (e.g. GRBL controller.open())."""
    _emit(ctx, device_mod.connect(ctx.obj["backend"]))

@device.command("disconnect")
@click.pass_context
def device_disconnect(ctx: click.Context):
    """Close the active device's serial connection (controller.close())."""
    _emit(ctx, device_mod.disconnect(ctx.obj["backend"]))


# ── Export commands ──────────────────────────────────────────────────────────


@cli.group()
def export():
    """Export project."""


@export.command("svg")
@click.argument("path")
@click.option("--version", default="default", help="SVG version: default, plain, compressed.")
@click.pass_context
@mutating
def export_svg_cmd(ctx: click.Context, path: str, version: str):
    """Export project as SVG."""
    _emit(ctx, export_mod.export_svg(ctx.obj["backend"], path, version=version))


@export.command("svgz")
@click.argument("path")
@click.pass_context
@mutating
def export_svgz_cmd(ctx: click.Context, path: str):
    """Export project as compressed SVGZ."""
    _emit(ctx, export_mod.export_svgz(ctx.obj["backend"], path))


@export.command("png")
@click.argument("path")
@click.option("--dpi", default=300, type=int, help="Render DPI.")
@click.pass_context
@mutating
def export_png_cmd(ctx: click.Context, path: str, dpi: int):
    """Export project as PNG (requires wxPython renderer)."""
    try:
        _emit(ctx, export_mod.export_png(ctx.obj["backend"], path, dpi=dpi))
    except RuntimeError as exc:
        _emit(ctx, {"error": str(exc)})
        return {"error": str(exc)}


@export.command("gcode")
@click.argument("path")
@click.option("--allow-full-power", is_flag=True, default=False, help="Allow export even if an operation is still at default power 1000.")
@click.pass_context
@mutating
def export_gcode_cmd(ctx: click.Context, path: str, allow_full_power: bool):
    """Export project as G-code (best-effort)."""
    try:
        _emit(ctx, export_mod.export_gcode(ctx.obj["backend"], path, allow_full_power=allow_full_power))
    except RuntimeError as exc:
        _emit(ctx, {"error": str(exc)})
        return {"error": str(exc)}
# ── Console passthrough ───────────────────────────────────────────────────────


@cli.command("console")
@click.argument("command")
@click.pass_context
def console_cmd(ctx: click.Context, command: str):
    """Pass a raw command to the MeerK40t console."""
    out = ctx.obj["backend"].run(command)
    if ctx.obj.get("json"):
        _emit(ctx, {"command": command, "output": out})
    else:
        for line in out:
            click.echo(line)


# ── Session commands ───────────────────────────────────────────────────────────


@cli.group(name="session")
def session_group():
    """Session management."""


@session_group.command("undo")
@click.pass_context
def session_undo(ctx: click.Context):
    """Undo the last command."""
    sess = ctx.obj.get("session")
    if not sess:
        _emit(ctx, {"error": "No active session"})
        return
    cmd = sess.undo()
    if cmd is None:
        _emit(ctx, {"error": "Nothing to undo"})
    else:
        _emit(ctx, {"undone": cmd})


@session_group.command("redo")
@click.pass_context
def session_redo(ctx: click.Context):
    """Redo the last undone command."""
    sess = ctx.obj.get("session")
    if not sess:
        _emit(ctx, {"error": "No active session"})
        return
    cmd = sess.redo()
    if cmd is None:
        _emit(ctx, {"error": "Nothing to redo"})
    else:
        _emit(ctx, {"redone": cmd})


@session_group.command("history")
@click.pass_context
def session_history(ctx: click.Context):
    """Show command history."""
    sess = ctx.obj.get("session")
    if not sess:
        _emit(ctx, {"error": "No active session"})
        return
    _emit(ctx, sess.history)


@session_group.command("status")
@click.pass_context
def session_status(ctx: click.Context):
    """Show session status."""
    sess = ctx.obj.get("session")
    if not sess:
        _emit(ctx, {"error": "No active session"})
        return
    _emit(ctx, sess.status())


# ── REPL ─────────────────────────────────────────────────────────────────────


@cli.command("repl", cls=click.Command)
@click.pass_context
def repl(ctx: click.Context):
    """Run the interactive REPL."""
    from cli_anything.meerk40t import __version__

    skin = ReplSkin("meerk40t", version=__version__)
    skin.print_banner()
    pt = skin.create_prompt_session()
    commands = {name: cmd.help or "" for name, cmd in cli.commands.items()}
    backend = ctx.obj["backend"]
    sess = ctx.obj.get("session")

    def _project_name():
        return sess.name if sess else ""

    def _modified():
        return sess.modified if sess else False

    while True:
        try:
            line = skin.get_input(pt, project_name=_project_name(), modified=_modified())
            if line is None or line.strip() in ("exit", "quit", "q"):
                break
            stripped = line.strip()
            if not stripped:
                continue
            if stripped == "help":
                skin.help(commands)
                continue
            if stripped == "status":
                _emit(ctx, project_mod.project_info(backend))
                continue
            _dispatch_repl(ctx, line, skin, commands)
        except (EOFError, KeyboardInterrupt):
            break
    skin.print_goodbye()


_REPL_READONLY = {
    ("project", "info"),
    ("project", "close"),
    ("elements", "list"),
    ("elements", "help"),
    ("operations", "list"),
    ("operations", "help"),
    ("device", "list"),
    ("device", "status"),
    ("device", "info"),
    ("device", "help"),
    ("export", "svg"),
    ("export", "svgz"),
    ("export", "png"),
    ("export", "gcode"),
    ("export", "help"),
    ("session", "status"),
    ("session", "history"),
    ("session", "help"),
}


def _repl_is_readonly(group: str, sub: str) -> bool:
    """True for REPL commands that do not mutate the project state."""
    if group == "help":
        return True
    return (group, sub) in _REPL_READONLY

def _dispatch_repl(ctx: click.Context, line: str, skin: ReplSkin, commands: dict[str, str]) -> int:
    """Token-based REPL dispatcher that calls the core modules directly."""
    tokens = line.split()
    group = tokens[0]
    rest = tokens[1:]
    backend = ctx.obj["backend"]
    sess = ctx.obj.get("session")
    result: Any = None
    # Capture all emitted output so the completion boundary can emit exactly
    # once, after persistence is proven, and discard it on failure.
    _begin_capture(ctx)

    try:
        if group == "console":
            command = " ".join(rest)
            result = {"command": command, "output": backend.run(command)}
        elif group == "project":
            sub = rest[0] if rest else "help"
            args = rest[1:]
            if sub == "new":
                name = args[0] if args else "Untitled"
                result = project_mod.create_project(backend, name=name)
                if sess:
                    sess.name = name
                    sess.modified = True
            elif sub == "open":
                path = args[0]
                result = project_mod.open_project(backend, path)
                # Guard rebind on success only (mirrors project_open): a failed
                # open must keep the prior binding so the next autosave cannot
                # write the current scene into the failed target (AC2/AC3).
                if result.get("error") is None:
                    ctx.obj["project_path"] = path
                    if sess:
                        sess.name = os.path.basename(path)
                        sess.svg_path = path
            elif sub == "save":
                path = args[0]
                version = args[1] if len(args) > 1 else "default"
                result = project_mod.save_project(backend, path, version=version)
                if result.get("error") is None:
                    ctx.obj["project_path"] = path
                    if sess:
                        sess.name = os.path.basename(path)
                        sess.svg_path = path
                        sess.modified = False
            elif sub == "info":
                result = project_mod.project_info(backend)
            elif sub == "close":
                result = project_mod.close_project(backend)
                if result.get("error") is None:
                    # Drop the binding so autosave writes no stale project SVG.
                    ctx.obj["project_path"] = None
                    if sess:
                        sess.svg_path = None
                        sess.modified = False
            else:
                result = {"error": f"Unknown project command: {sub}"}
        elif group == "elements":
            sub = rest[0] if rest else "help"
            args = rest[1:]
            if sub == "circle":
                cx, cy, r = args[0], args[1], args[2]
                result = elements_mod.add_circle(backend, cx, cy, r)
            elif sub == "rect":
                x, y, w, h = args[0], args[1], args[2], args[3]
                result = elements_mod.add_rect(backend, x, y, w, h)
            elif sub == "ellipse":
                cx, cy, rx, ry = args[0], args[1], args[2], args[3]
                result = elements_mod.add_ellipse(backend, cx, cy, rx, ry)
            elif sub == "line":
                x1, y1, x2, y2 = args[0], args[1], args[2], args[3]
                result = elements_mod.add_line(backend, x1, y1, x2, y2)
            elif sub == "polyline":
                if len(args) % 2 != 0:
                    result = {"error": "Polyline requires an even number of coordinates"}
                else:
                    points = [(args[i], args[i + 1]) for i in range(0, len(args), 2)]
                    result = elements_mod.add_polyline(backend, points)
            elif sub == "text":
                x, y = args[0], args[1]
                text = " ".join(args[2:])
                result = elements_mod.add_text(backend, x, y, text)
            elif sub == "list":
                result = elements_mod.list_elements(backend)
            elif sub == "delete":
                result = elements_mod.delete_element(backend, int(args[0]))
            elif sub == "select":
                result = elements_mod.select_element(backend, int(args[0]))
            elif sub == "clear":
                result = elements_mod.clear_elements(backend)
            elif sub == "frame":
                result = elements_mod.frame(backend)
            elif sub == "translate":
                index = int(args[0])
                tx = args[1]
                ty = args[2]
                absolute = "--absolute" in args or "-a" in args
                result = elements_mod.translate_element(backend, index, tx, ty, absolute=absolute)
            elif sub == "scale":
                index = int(args[0])
                scale_x = args[1]
                scale_y = None
                absolute = False
                px = None
                py = None
                rem = args[2:]
                if rem and not rem[0].startswith("-"):
                    scale_y = rem[0]
                    rem = rem[1:]
                i = 0
                while i < len(rem):
                    opt = rem[i]
                    if opt in ("--absolute", "-a"):
                        absolute = True
                        i += 1
                    elif opt in ("-x", "--px") and i + 1 < len(rem):
                        px = rem[i+1]
                        i += 2
                    elif opt in ("-y", "--py") and i + 1 < len(rem):
                        py = rem[i+1]
                        i += 2
                    else:
                        i += 1
                result = elements_mod.scale_element(backend, index, scale_x, scale_y, absolute=absolute, px=px, py=py)
            elif sub == "rotate":
                index = int(args[0])
                angle = args[1]
                absolute = False
                cx = None
                cy = None
                rem = args[2:]
                i = 0
                while i < len(rem):
                    opt = rem[i]
                    if opt in ("--absolute", "-a"):
                        absolute = True
                        i += 1
                    elif opt in ("-x", "--cx") and i + 1 < len(rem):
                        cx = rem[i+1]
                        i += 2
                    elif opt in ("-y", "--cy") and i + 1 < len(rem):
                        cy = rem[i+1]
                        i += 2
                    else:
                        i += 1
                result = elements_mod.rotate_element(backend, index, angle, absolute=absolute, cx=cx, cy=cy)
            elif sub == "align":
                mode = args[0]
                indexes = []
                rem = args[1:]
                i = 0
                while i < len(rem):
                    if rem[i] in ("-i", "--index") and i + 1 < len(rem):
                        indexes.append(int(rem[i+1]))
                        i += 2
                    else:
                        i += 1
                result = elements_mod.align_elements(backend, mode, indexes=indexes if indexes else None)
            elif sub == "group":
                label = None
                indexes = []
                rem = args
                i = 0
                while i < len(rem):
                    if rem[i] in ("-l", "--label") and i + 1 < len(rem):
                        label = rem[i+1]
                        i += 2
                    elif rem[i] in ("-i", "--index") and i + 1 < len(rem):
                        indexes.append(int(rem[i+1]))
                        i += 2
                    else:
                        i += 1
                result = elements_mod.group_elements(backend, label, indexes=indexes if indexes else None)
            elif sub == "ungroup":
                index = None
                rem = args
                i = 0
                while i < len(rem):
                    if rem[i] in ("-i", "--index") and i + 1 < len(rem):
                        index = int(rem[i+1])
                        i += 2
                    else:
                        i += 1
                result = elements_mod.ungroup_elements(backend, index=index)
            else:
                result = {"error": f"Unknown elements command: {sub}"}
        elif group == "operations":
            sub = rest[0] if rest else "help"
            args = rest[1:]
            if sub == "list":
                result = operations_mod.list_operations(backend)
            elif sub == "add":
                result = operations_mod.add_operation(backend, args[0])
            elif sub == "classify":
                result = operations_mod.classify_elements(backend)
            elif sub == "declassify":
                result = operations_mod.declassify_elements(backend)
            elif sub == "set":
                result = operations_mod.set_operation(backend, int(args[0]), args[1], args[2])
            elif sub == "delete":
                result = operations_mod.delete_operation(backend, int(args[0]))
            elif sub == "clear":
                result = operations_mod.clear_operations(backend)
            else:
                result = {"error": f"Unknown operations command: {sub}"}
        elif group == "device":
            sub = rest[0] if rest else "help"
            args = rest[1:]
            if sub == "list":
                result = device_mod.list_devices(backend)
            elif sub == "status":
                result = device_mod.device_status(backend)
            elif sub == "home":
                result = device_mod.home(backend)
            elif sub == "physical-home":
                result = device_mod.physical_home(backend)
            elif sub == "move":
                absolute = True
                if args and args[0] in ("--relative", "-r"):
                    absolute = False
                    args = args[1:]
                x, y = args[0], args[1]
                result = device_mod.move(backend, x, y, absolute=absolute)
            elif sub == "info":
                result = device_mod.device_info(backend)
            elif sub == "connect":
                result = device_mod.connect(backend)
            elif sub == "disconnect":
                result = device_mod.disconnect(backend)
            else:
                result = {"error": f"Unknown device command: {sub}"}
        elif group == "export":
            sub = rest[0] if rest else "help"
            args = rest[1:]
            if sub == "svg":
                path = args[0]
                version = args[1] if len(args) > 1 else "default"
                result = export_mod.export_svg(backend, path, version=version)
            elif sub == "svgz":
                result = export_mod.export_svgz(backend, args[0])
            elif sub == "png":
                dpi = 300
                if args[0] == "--dpi":
                    dpi = int(args[1])
                    path = args[2]
                else:
                    path = args[0]
                try:
                    result = export_mod.export_png(backend, path, dpi=dpi)
                except RuntimeError as exc:
                    result = {"error": str(exc)}
            elif sub == "gcode":
                result = export_mod.export_gcode(backend, args[0])
            else:
                result = {"error": f"Unknown export command: {sub}"}
        elif group == "session":
            sub = rest[0] if rest else "status"
            if not sess:
                result = {"error": "No active session"}
            elif sub == "undo":
                cmd = sess.undo()
                result = {"undone": cmd} if cmd is not None else {"error": "Nothing to undo"}
            elif sub == "redo":
                cmd = sess.redo()
                result = {"redone": cmd} if cmd is not None else {"error": "Nothing to redo"}
            elif sub == "history":
                result = sess.history
            elif sub == "status":
                result = sess.status()
            else:
                result = {"error": f"Unknown session command: {sub}"}
        else:
            result = {"error": f"Unknown command group: {group}"}
    except Exception as exc:
        result = {"error": str(exc) or exc.__class__.__name__, "category": "infrastructure"}

    # Route through the single completion boundary: classify, auto-save once
    # after a proven successful mutation, emit buffered output exactly once, and
    # record the command only after persistence is confirmed. Read-only queries
    # (info/status/list/history/help and exports) do not auto-save.
    is_mutating = not _repl_is_readonly(group, rest[0] if rest else "help")
    if result is not None:
        _emit(ctx, result)
    return _complete_command(
        ctx,
        result,
        mutating=is_mutating,
        repl=True,
        on_success=(lambda: sess.record_command(line)) if (sess and is_mutating) else None,
    )


# ── Preflight (shared by `job preflight` and `attach stage`) ────────────────


OPERATOR_CHECKLIST = [
    "sheet placed + origin confirmed",
    "overhang supported flat",
    "diode focused",
    "rated glasses on",
    "ventilation running",
    "extinguisher/fire blanket in reach",
    "operator stays for entire burn",
]


def _sha256_file(path: str) -> str | None:
    """Return the sha256 hex digest of a file, or None if it is missing/unreadable."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _run_preflight(
    manifest_path: str, *, allow_estimated: bool, stage_mode: bool = False
) -> tuple[dict, int]:
    """Re-verify a job manifest. One code path for `job preflight` and `attach stage`.

    Returns ``(result, exit_code)``. Hard failures (hash mismatch, missing file,
    changed material settings, failed verification) are exit 1 for ``job
    preflight`` and exit 2 for ``attach stage`` (every staging refusal is an
    acknowledgeable gate). Estimated roles without ``--allow-estimated`` always
    take precedence as the exit-2 acknowledgeable gate. Ladder manifests skip
    the settings-fingerprint re-resolution (their fingerprint is null).
    """
    mpath = Path(manifest_path).resolve()
    if not mpath.exists():
        return (
            {"ok": False, "failures": [f"manifest not found: {mpath}"]},
            2 if stage_mode else 1,
        )
    try:
        manifest = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return (
            {"ok": False, "failures": [f"manifest unreadable: {exc}"]},
            2 if stage_mode else 1,
        )
    if not isinstance(manifest, dict):
        return (
            {
                "ok": False,
                "failures": [
                    f"manifest root must be a JSON object, got {type(manifest).__name__}"
                ],
            },
            2 if stage_mode else 1,
        )
    schema = manifest.get("schema")
    if schema != "clia-job-manifest-v1":
        return (
            {
                "ok": False,
                "failures": [
                    f"unknown or missing manifest schema: {schema!r} "
                    "(expected 'clia-job-manifest-v1')"
                ],
            },
            2 if stage_mode else 1,
        )

    is_ladder = manifest.get("kind") == "ladder"
    failures: list[str] = []
    warnings: list[str] = []

    # 1. Recompute sha256 for every file recorded at prepare time.
    files = manifest.get("files", {})
    for fname in ("input_svg", "job_svg", "gcode"):
        entry = files.get(fname) or {}
        path = entry.get("path")
        recorded = entry.get("sha256")
        if not path:
            failures.append(f"manifest missing files.{fname}")
            continue
        actual = _sha256_file(path)
        if actual is None:
            failures.append(f"{fname} missing: {path} - regenerate with job prepare")
        elif actual != recorded:
            failures.append(f"{fname} hash mismatch: {path} - regenerate with job prepare")

    # 2. Re-resolve material + machine and recompute the settings fingerprint.
    #    Ladder manifests have no settings_fingerprint to check.
    if not is_ladder:
        fingerprint = manifest.get("settings_fingerprint")
        machine = manifest.get("machine")
        material = manifest.get("material")
        try:
            mat = materials_mod.load_material(material) if material else None
            if mat is None:
                failures.append(
                    f"material {material!r} no longer available - re-run job prepare"
                )
            else:
                roles = materials_mod.resolve_settings(mat, machine)
                payload = json.dumps(roles, sort_keys=True)
                actual_fp = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                if actual_fp != fingerprint:
                    failures.append(
                        "material settings changed since prepare - re-run job prepare"
                    )
        except ValueError as exc:
            failures.append(f"{exc} - re-run job prepare")

    # 3. Re-check the recorded verification verdict.
    verification = manifest.get("verification", {})
    if not verification.get("all_passed"):
        failures.append(
            "recorded verification did not pass - regenerate with job prepare"
        )

    # 4. Estimated-role gate - provenance comes from the TRUSTED material store,
    #    never from the manifest's recorded estimated_roles. We take the set of
    #    roles the job processed from the manifest operations, re-resolve their
    #    provenance from the material store, and treat any non-"tested" role as
    #    estimated. A disagreement with the recorded estimated_roles means the
    #    manifest was tampered with. (The manifest is attacker-editable, so this
    #    detects casual edits, not a forged manifest that changes operations and
    #    estimated_roles together; the material store remains the trust root.)
    #    Ladder manifests carry no roles, so the gate does not apply.
    reevaluated_estimated: set[str] = set()
    if not is_ladder:
        ops = manifest.get("operations", []) or []
        present_roles: set[str] | None = {
            op["role"]
            for op in ops
            if isinstance(op, dict) and op.get("role")
        }
        if not present_roles:
            # Older manifests recorded no per-operation role; fall back to every
            # resolved role rather than risk a false tamper rejection.
            present_roles = None
        recorded_estimated = set(manifest.get("estimated_roles", []) or [])
        mat_gate = materials_mod.load_material(material) if material else None
        if mat_gate is not None:
            try:
                resolved = materials_mod.resolve_settings(mat_gate, machine)
            except ValueError:
                resolved = None
            if resolved is not None:
                for role, rset in resolved.items():
                    if present_roles is not None and role not in present_roles:
                        continue
                    if rset.get("provenance") != "tested":
                        reevaluated_estimated.add(role)
                if reevaluated_estimated != recorded_estimated:
                    return (
                        {
                            "ok": False,
                            "failures": [
                                "manifest estimated_roles tampered: recorded "
                                f"{sorted(recorded_estimated)} but the material store "
                                f"resolves {sorted(reevaluated_estimated)}"
                            ],
                        },
                        2 if stage_mode else 1,
                    )
    reevaluated_estimated_list = sorted(reevaluated_estimated)
    if reevaluated_estimated_list and not allow_estimated:
        gate = list(failures)
        gate.append(
            f"estimated roles {reevaluated_estimated_list} require --allow-estimated"
        )
        return (
            {"ok": False, "failures": gate, "estimated_roles": reevaluated_estimated_list},
            2,
        )
    if reevaluated_estimated_list:
        warnings.append(
            f"estimated roles acknowledged under --allow-estimated: {reevaluated_estimated_list}"
        )

    hard_exit = 2 if stage_mode else 1
    if failures:
        return ({"ok": False, "failures": failures}, hard_exit)

    checklist = list(OPERATOR_CHECKLIST)
    if is_ladder:
        checklist.append("burn on SCRAP of the target material only")
    return (
        {
            "ok": True,
            "manifest": str(mpath),
            "kind": manifest.get("kind", "job"),
            "machine": manifest.get("machine"),
            "material": manifest.get("material"),
            "estimated_roles": reevaluated_estimated_list,
            "operations": manifest.get("operations", []),
            "warnings": warnings,
            "checklist": checklist,
        },
        0,
    )


# ── materials group ─────────────────────────────────────────────────────────


@cli.group()
def materials():
    """Material profile management (calibrated laser settings per machine)."""


@materials.command("list")
@click.pass_context
def materials_list(ctx):
    """List available materials (bundled and user)."""
    _emit(ctx, {"materials": materials_mod.list_materials()})


@materials.command("show")
@click.argument("name")
@click.option("--machine", "machine", default=None, help="Show only this machine's role settings.")
@click.pass_context
def materials_show(ctx, name, machine):
    """Show a material profile, or one machine's role settings with --machine."""
    material = materials_mod.load_material(name)
    if material is None:
        _emit(
            ctx,
            {
                "error": f"unknown material: {name!r}",
                "known": materials_mod.available_names(),
            },
        )
        sys.stdout = _REAL_STDOUT
        ctx.exit(1)
    if machine is not None:
        try:
            roles = materials_mod.resolve_settings(material, machine)
        except ValueError as exc:
            _emit(ctx, {"error": str(exc)})
            sys.stdout = _REAL_STDOUT
            ctx.exit(1)
        _emit(ctx, {"machine": machine, "roles": roles})
        return
    _emit(ctx, material)


@materials.command("create")
@click.argument("name")
@click.option("--description", "description", required=True, help="Human-readable description.")
@click.option("--machine", "machine", default=None, help="Create an empty machine section.")
@click.pass_context
def materials_create(ctx, name, description, machine):
    """Create an empty user material (no role settings yet)."""
    if materials_mod.load_material(name) is not None:
        _emit(ctx, {"error": f"material {name!r} already exists"})
        sys.stdout = _REAL_STDOUT
        ctx.exit(1)
    machines: dict[str, dict] = {}
    if machine is not None:
        machines[machine] = {"roles": {}}
    data = {"name": name, "description": description, "machines": machines}
    path = materials_mod.save_user_material(name, data)
    _emit(
        ctx,
        {
            "created": name,
            "path": str(path),
            "next": "roles are empty - run 'job ladder' on scrap, then 'materials record' to add calibrated settings",
        },
    )


@materials.command("record")
@click.argument("name")
@click.option("--machine", "machine", required=True, help="Machine name.")
@click.option("--role", "role", type=ROLE_CHOICES, required=True, help="Role to record (cut, score, etch).")
@click.option("--power", "power", type=click.IntRange(1, 1000), required=True, help="Laser power (S value, 1..1000).")
@click.option("--speed", "speed", type=click.FloatRange(min=0, min_open=True), required=True, help="Feed rate in mm/s (>0).")
@click.option("--passes", "passes", type=click.IntRange(1, None), required=True, help="Number of passes (>=1).")
@click.option("--provenance", "provenance", type=PROVENANCE_CHOICES, required=True, help="tested (observed) or estimated (rationale).")
@click.option("--note", "note", required=True, help="Evidence (tested) or rationale (estimated) for the setting.")
@click.pass_context
def materials_record(ctx, name, machine, role, power, speed, passes, provenance, note):
    """Record a calibrated (or estimated) role setting into a user material."""
    # Evidence rule: tested settings require a note describing the observation.
    # Checked before any load/write so a refused record writes nothing.
    if provenance == "tested" and len(note) < 20:
        _emit(
            ctx,
            {
                "error": "tested provenance requires evidence: describe the test burn in --note (material, pattern, settings, observation, date)"
            },
        )
        sys.stdout = _REAL_STDOUT
        ctx.exit(1)
    material = materials_mod.load_material(name)
    if material is None:
        _emit(
            ctx,
            {
                "error": f"unknown material: {name!r}",
                "known": materials_mod.available_names(),
            },
        )
        sys.stdout = _REAL_STDOUT
        ctx.exit(1)
    updated = copy.deepcopy(material)
    machine_section = updated.setdefault("machines", {}).setdefault(machine, {"roles": {}})
    roles = machine_section.setdefault("roles", {})
    roles[role] = {
        "kind": "cut" if role == "cut" else "engrave",
        "passes": passes,
        "power": power,
        "speed": speed,
        "provenance": provenance,
        "note": note,
    }
    path = materials_mod.save_user_material(name, updated)
    _emit(
        ctx,
        {
            "recorded": name,
            "machine": machine,
            "role": role,
            "path": str(path),
            "settings": roles[role],
        },
    )


# ── job group ───────────────────────────────────────────────────────────────


@cli.group()
def job():
    """Laser job preparation: prepare, preflight, and calibration ladders."""


@job.command("prepare")
@click.argument("input_svg")
@click.option("--out-dir", "out_dir", required=True, help="Directory for job artefacts.")
@click.option("--material", "material", default=None, help="Material profile name.")
@click.option("--allow-estimated", is_flag=True, default=False, help="Allow untested (estimated) settings through the gate.")
@click.option("--map", "color_maps", multiple=True, help="Colour=role mapping (e.g. #ff0000=cut). Replaces the default map wholesale.")
@click.pass_context
def job_prepare(ctx, input_svg, out_dir, material, allow_estimated, color_maps):
    """Prepare a verified job SVG + G-code + manifest from a design SVG."""
    machine = ctx.obj.get("machine_name")
    if not machine:
        _emit(ctx, {"error": "--machine NAME is required for job prepare"})
        sys.stdout = _REAL_STDOUT
        ctx.exit(1)
    if not material:
        _emit(ctx, {"error": "--material NAME is required for job prepare"})
        sys.stdout = _REAL_STDOUT
        ctx.exit(1)

    color_map = None
    if color_maps:
        color_map = {}
        for item in color_maps:
            if "=" not in item:
                _emit(ctx, {"error": f"invalid --map {item!r}; expected #rrggbb=role"})
                sys.stdout = _REAL_STDOUT
                ctx.exit(1)
            color, role = item.split("=", 1)
            color_map[color.strip()] = role.strip()

    try:
        summary = job_prep_mod.prepare_job(
            input_svg,
            out_dir,
            machine=machine,
            material=material,
            color_map=color_map,
            allow_estimated=allow_estimated,
        )
    except job_prep_mod.UncalibratedSettingsError as exc:
        _emit(ctx, {"error": str(exc), "estimated_roles": list(exc.estimated_roles)})
        sys.stdout = _REAL_STDOUT
        ctx.exit(2)
    except (job_prep_mod.MissingRoleError, job_prep_mod.JobPrepError) as exc:
        _emit(ctx, {"error": str(exc)})
        sys.stdout = _REAL_STDOUT
        ctx.exit(1)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        _emit(ctx, {"error": str(exc)})
        sys.stdout = _REAL_STDOUT
        ctx.exit(1)

    if not summary.get("verification", {}).get("all_passed"):
        _emit(ctx, summary)
        sys.stdout = _REAL_STDOUT
        ctx.exit(1)
    _emit(ctx, summary)


@job.command("preflight")
@click.argument("manifest")
@click.option("--allow-estimated", is_flag=True, default=False, help="Acknowledge estimated-role settings.")
@click.pass_context
def job_preflight(ctx, manifest, allow_estimated):
    """Re-verify a job manifest: file hashes, settings fingerprint, verification."""
    result, code = _run_preflight(manifest, allow_estimated=allow_estimated)
    _emit(ctx, result)
    if code != 0:
        sys.stdout = _REAL_STDOUT
        ctx.exit(code)


@job.command("ladder")
@click.option("--out-dir", "out_dir", required=True, help="Directory for ladder artefacts.")
@click.option("--role", "role", type=ROLE_CHOICES, required=True, help="Role to calibrate (cut, score, etch).")
@click.option("--powers", "powers", required=True, help="Comma-separated power values, e.g. 550,650,750.")
@click.option("--speed", "speed", type=float, required=True, help="Feed rate in mm/s.")
@click.option("--passes", "passes", type=int, default=1, show_default=True, help="Passes per line.")
@click.option("--length", "length", type=float, default=20.0, show_default=True, help="Line length in mm.")
@click.option("--pitch", "pitch", type=float, default=6.0, show_default=True, help="Vertical pitch between lines in mm.")
@click.pass_context
def job_ladder(ctx, out_dir, role, powers, speed, passes, length, pitch):
    """Generate a calibration ladder (one line per power) for a role."""
    machine = ctx.obj.get("machine_name")
    if not machine:
        _emit(ctx, {"error": "--machine NAME is required for job ladder"})
        sys.stdout = _REAL_STDOUT
        ctx.exit(1)

    # Parse powers before the kernel starts so bad input is a clean error.
    parsed_powers: list[int] = []
    for token in powers.split(","):
        token = token.strip()
        if token == "":
            continue
        try:
            parsed_powers.append(int(token))
        except ValueError:
            _emit(ctx, {"error": f"invalid --powers entry {token!r}; expected integers"})
            sys.stdout = _REAL_STDOUT
            ctx.exit(1)
    if not parsed_powers:
        _emit(ctx, {"error": "--powers must contain at least one power value"})
        sys.stdout = _REAL_STDOUT
        ctx.exit(1)

    try:
        summary = job_prep_mod.prepare_ladder(
            out_dir,
            machine=machine,
            role=role,
            powers=parsed_powers,
            speed=speed,
            passes=passes,
            length=length,
            pitch=pitch,
        )
    except job_prep_mod.JobPrepError as exc:
        _emit(ctx, {"error": str(exc)})
        sys.stdout = _REAL_STDOUT
        ctx.exit(1)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        _emit(ctx, {"error": str(exc)})
        sys.stdout = _REAL_STDOUT
        ctx.exit(1)

    table = [{"line": i + 1, "power": p} for i, p in enumerate(parsed_powers)]
    _emit(
        ctx,
        {
            "role": role,
            "machine": machine,
            "powers": parsed_powers,
            "table": table,
            "summary": summary,
            "next": (
                "after burning, record the winning step: cli-anything-meerk40t "
                f"materials record <name> --machine {machine} --role {role} "
                f"--power <P> --speed {speed} --passes {passes} "
                "--provenance tested --note '<what you burned and saw, with date>'"
            ),
        },
    )


# ── attach group (thin client over the consoleserver control channel) ───────


@cli.group()
@click.option("--host", "host", default="localhost", show_default=True, help="consoleserver host.")
@click.option("--port", "port", default=2323, type=int, show_default=True, help="consoleserver port.")
@click.pass_context
def attach(ctx, host, port):
    """Drive the running MeerK40t GUI kernel over the consoleserver control channel."""
    ctx.obj["attach_host"] = host
    ctx.obj["attach_port"] = port


def _attach_send(ctx, command):
    """Send a framed command, mapping any failure to a no-frame-style error dict."""
    try:
        return None, attach_client_mod.send(ctx.obj["attach_host"], ctx.obj["attach_port"], command)
    except attach_client_mod.AttachError as exc:
        return {"error": str(exc)}, None
    except OSError as exc:
        # Connection refused / unreachable: the control module is not reachable.
        return {
            "error": (
                "no #CLIA1# frame received - is the GUI running with the "
                f"cli-anything extension? (connection failed: {exc})"
            )
        }, None


@attach.command("status")
@click.pass_context
def attach_status(ctx):
    """Query the live kernel: devices, bed, element/op counts, spooler."""
    err, reply = _attach_send(ctx, "agent status")
    if err is not None:
        _emit(ctx, err)
        sys.stdout = _REAL_STDOUT
        ctx.exit(1)
    _emit(ctx, reply)


@attach.command("stage")
@click.argument("job_svg")
@click.argument("manifest")
@click.option("--allow-estimated", is_flag=True, default=False, help="Acknowledge estimated-role settings at the machine boundary.")
@click.pass_context
def attach_stage(ctx, job_svg, manifest, allow_estimated):
    """Verify a job manifest, then stage the job SVG on the live kernel."""
    job_svg_abs = str(Path(job_svg).resolve())
    mpath = Path(manifest).resolve()

    if not mpath.exists():
        _emit(ctx, {"ok": False, "failures": [f"manifest not found: {mpath}"]})
        sys.stdout = _REAL_STDOUT
        ctx.exit(2)
    try:
        manifest_data = json.loads(mpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _emit(ctx, {"ok": False, "failures": [f"manifest unreadable: {exc}"]})
        sys.stdout = _REAL_STDOUT
        ctx.exit(2)
    if not isinstance(manifest_data, dict):
        _emit(
            ctx,
            {
                "ok": False,
                "failures": [
                    f"manifest root must be a JSON object, got {type(manifest_data).__name__}"
                ],
            },
        )
        sys.stdout = _REAL_STDOUT
        ctx.exit(2)

    # The staged file must be the exact file whose hashes were verified: one
    # object carries the design, the verified hashes, and the material identity.
    recorded_job_svg = manifest_data.get("files", {}).get("job_svg", {}).get("path")
    if recorded_job_svg != job_svg_abs:
        _emit(
            ctx,
            {
                "ok": False,
                "failures": [
                    f"job SVG {job_svg_abs!r} does not match manifest files.job_svg.path {recorded_job_svg!r}"
                ],
            },
        )
        sys.stdout = _REAL_STDOUT
        ctx.exit(2)

    result, code = _run_preflight(manifest, allow_estimated=allow_estimated, stage_mode=True)
    if not result.get("ok"):
        _emit(ctx, result)
        sys.stdout = _REAL_STDOUT
        ctx.exit(code)

    # Stage over the control channel. The recorded sha256 of the job SVG and a
    # base64-encoded absolute path are two whitespace-free tokens, so a path
    # containing spaces survives intact and the kernel re-verifies the staged
    # bytes against the recorded hash before touching the scene.
    files = manifest_data.get("files", {})
    job_svg_entry = files.get("job_svg", {}) or {}
    expected_sha = job_svg_entry.get("sha256")
    b64_path = base64.b64encode(job_svg_abs.encode("utf-8")).decode("ascii")
    err, stage_reply = _attach_send(ctx, f"agent stage {expected_sha} {b64_path}")
    if err is not None:
        _emit(ctx, err)
        sys.stdout = _REAL_STDOUT
        ctx.exit(1)
    if isinstance(stage_reply, dict) and stage_reply.get("error") is not None:
        _emit(ctx, {"ok": False, "failures": [stage_reply["error"]]})
        sys.stdout = _REAL_STDOUT
        ctx.exit(2)

    _emit(
        ctx,
        {
            "staged": job_svg_abs,
            "manifest": str(mpath),
            "material": manifest_data.get("material"),
            "operations": stage_reply.get("operations", manifest_data.get("operations", [])),
        },
    )
if __name__ == "__main__":
    cli()
