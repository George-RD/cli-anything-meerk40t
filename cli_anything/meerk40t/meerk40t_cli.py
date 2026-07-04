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
from cli_anything.meerk40t.utils.repl_skin import ReplSkin


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


@click.group(cls=MeerkGroup, invoke_without_command=True)
@click.option("--json/--no-json", "json_mode", default=False, help="Output results as JSON.")
@click.option("--project", "-p", "project_path", default=None, help="Project SVG file to open.")
@click.option("--session", "-s", "session_path", default=None, help="Session JSON file.")
@click.option("--dry-run", is_flag=True, default=False, help="Do not auto-save after mutations.")
@click.pass_context
def cli(ctx: click.Context, json_mode: bool, project_path: str | None, session_path: str | None, dry_run: bool):
    """cli-anything-meerk40t — agent CLI for MeerK40t laser software."""
    ctx.ensure_object(dict)
    sys.stdout = _KernelSuppressor(_REAL_STDOUT)
    backend = Meerk40tBackend()
    backend.start()
    ctx.obj["backend"] = backend
    ctx.obj["json"] = json_mode
    ctx.obj["dry_run"] = dry_run
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
@click.pass_context
@mutating
def export_gcode_cmd(ctx: click.Context, path: str):
    """Export project as G-code (best-effort)."""
    _emit(ctx, export_mod.export_gcode(ctx.obj["backend"], path))


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
