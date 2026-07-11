"""cli-anything-meerk40t — Click CLI + REPL for the MeerK40t kernel."""

from __future__ import annotations

import functools
import json
import os
import sys
from typing import Any

import click

from cli_anything.meerk40t.core import device as device_mod
from cli_anything.meerk40t.core import elements as elements_mod
from cli_anything.meerk40t.core import export as export_mod
from cli_anything.meerk40t.core import operations as operations_mod
from cli_anything.meerk40t.core import project as project_mod
from cli_anything.meerk40t.core import session as session_mod
from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend
from cli_anything.meerk40t.utils import profiles as profiles_mod
from cli_anything.meerk40t.utils import submit as submit_mod
from cli_anything.meerk40t.utils.repl_skin import ReplSkin
# Driver choices for --device. Extracted to a module-level var so the
# skill_generator regex (which chokes on nested parens in decorators) does
# not trip over click.Choice([...]).
DEVICE_CHOICES = click.Choice(
    ["dummy", "grbl", "lihuiyu", "moshi", "ruida", "newly", "balor"]
)


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


def _emit(ctx: click.Context, data: Any) -> None:
    """Emit a result as JSON or human-readable text depending on --json."""
    if ctx.obj.get("json"):
        click.echo(json.dumps(data, indent=2, default=str))
    else:
        _emit_human(data)


# ── Auto-save helper ──────────────────────────────────────────────────────────


def _auto_save(ctx: click.Context) -> None:
    """Save the session (and backing SVG) after a mutating command."""
    if ctx.obj.get("dry_run"):
        return
    backend = ctx.obj.get("backend")
    sess = ctx.obj.get("session")
    project_path = ctx.obj.get("project_path")
    if sess and getattr(sess, "svg_path", None):
        sess.save(backend)
    elif project_path:
        # Persist back to the project SVG directly when --project was given.
        try:
            backend.save_svg(project_path)
        except Exception:
            pass


def mutating(f):
    """Decorator that auto-saves the active session after a mutating command."""

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        ctx = click.get_current_context()
        result = f(*args, **kwargs)
        _auto_save(ctx)
        return result

    return wrapper


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

    backend = Meerk40tBackend(device=device, port=port, baud=baud)
    backend.start()
    ctx.obj["backend"] = backend

    # Apply the profile's bed dimensions to the active device view.
    if profile is not None:
        _apply_machine_profile(backend, profile)
    ctx.obj["project_path"] = project_path

    if session_path:
        sess = session_mod.Session(session_path)
    else:
        sess = None
    ctx.obj["session"] = sess

    if project_path:
        project_mod.open_project(backend, project_path)
        if sess:
            sess.name = os.path.basename(project_path)
            sess.svg_path = project_path

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
    sess = ctx.obj.get("session")
    if sess:
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
        if ctx.obj.get("json"):
            click.echo(json.dumps({"error": str(exc)}, indent=2))
        else:
            click.echo(f"Error: {exc}", err=True)


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
        if ctx.obj.get("json"):
            click.echo(json.dumps({"error": str(exc)}, indent=2))
        else:
            click.echo(f"Error: {exc}", err=True)


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


@cli.command("repl")
@click.pass_context
def repl(ctx: click.Context):
    """Run the interactive REPL."""
    skin = ReplSkin("meerk40t", version="1.0.0")
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


def _dispatch_repl(ctx: click.Context, line: str, skin: ReplSkin, commands: dict[str, str]) -> None:
    """Token-based REPL dispatcher that calls the core modules directly."""
    tokens = line.split()
    group = tokens[0]
    rest = tokens[1:]
    backend = ctx.obj["backend"]
    sess = ctx.obj.get("session")
    result: Any = None

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
                if sess:
                    sess.name = os.path.basename(path)
                    sess.svg_path = path
            elif sub == "save":
                path = args[0]
                version = args[1] if len(args) > 1 else "default"
                result = project_mod.save_project(backend, path, version=version)
                if sess:
                    sess.name = os.path.basename(path)
                    sess.svg_path = path
                    sess.modified = False
            elif sub == "info":
                result = project_mod.project_info(backend)
            elif sub == "close":
                result = project_mod.close_project(backend)
                if sess:
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
        result = {"error": str(exc)}

    # Record and auto-save for the REPL unless it is a non-mutating query.
    if sess and line.strip() not in ("help", "status"):
        sess.record_command(line)
    if sess and not ctx.obj.get("dry_run") and getattr(sess, "svg_path", None):
        try:
            sess.save(backend)
        except Exception:
            pass

    _emit(ctx, result)


if __name__ == "__main__":
    cli()
