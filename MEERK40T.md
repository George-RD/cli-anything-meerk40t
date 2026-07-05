# MeerK40t — Agent CLI Harness SOP

## Software Overview

MeerK40t is a plugin-based laser cutting/engraving control application built around a
**Kernel** service bus. The kernel provides a console command system, signals,
channels, services, and a plugin lifecycle.

- **Backend engine**: `meerk40t.kernel.Kernel` — central service bus with a console
  parser, signal system, channel messaging, and a plugin lifecycle.
- **Native format**: SVG (load/save via `meerk40t/core/svg_io.py`). DXF is also
  supported via `meerk40t/dxf/`. The elements tree is the project state.
- **Device drivers**: `grbl`, `lihuiyu`, `ruida`, `moshi`, `newly`, `balormk`.
- **Headless mode**: `meerk40t -z` runs without GUI; `-e "<command>"` executes a
  single console command; `-c` starts an interactive console; `-b <file>` runs a
  batch file. The CLI and GUI are separate processes by default.

## GUI Actions → Console Commands

The kernel console is the API surface. Key command groups (executed via
`kernel.console("<command>\n")`):

| Domain | Console commands |
|---|---|
| Elements | `circle`, `rect`, `ellipse`, `line`, `polyline`, `path`, `text`, `image`, `frame`, `grid`, `element*` |
| Tree/Operations | `tree`, `element`, `op`, `classify`, `clipboard`, `align`, `group`, `ungroup` |
| Files | `load <path>`, `save <path>`, `export <path>` |
| Device | `device`, `service device ...`, `set`, `flush`, `home`, `physical_home`, `move`, `move_absolute`, `devinfo` |
| Spooler | `plan`, `spooler`, `job`, `execute` |
| Network | `consoleserver -p <port>`, `webserver -p <port>` |
| Settings | `set <key> <value>`, `flush`, `bind`, `alias` |
| Help | `help`, `help <command>` |

## Backend Integration

The harness wraps the **real MeerK40t kernel** as the backend. It boots a headless
kernel instance (the same code path as `meerk40t -z`) and drives it via
`kernel.console()`. Channel output is captured by watching the `console` channel.

- **Executable**: `meerk40t` (CLI entry) or `python -m meerk40t.main`.
- **Headless bootstrap**: replicate `test/bootstrap.py` — create a `Kernel`, add the
  core + device + svg plugins, call `kernel(partial=True)`, then drive via
  `kernel.console()`.
- **Render/export**: `save <path.svg>` writes SVG via the real `SVGWriter`;
  `export <path.png>` triggers the real raster renderer.

## Data Model

- **Project file**: SVG (XML). The elements tree is serialized to/from SVG.
- **Session state**: open file path, modified flag, undo history, device selection,
  spooler state. Persisted as JSON alongside the SVG.
- **Units**: native units are `UNITS_PER_MIL` (1000 per mil = 39370 per inch).
  Console commands accept `mm`, `cm`, `in`, `mil`, `px`, `steps`.

## CLI Command Groups

1. `project` — new, open, save, info, close (SVG project files)
2. `elements` — add shapes (circle, rect, line, text, etc.), list, select, delete, translate, scale, rotate, align, group, ungroup
3. `operations` — list/add/set cut/engrave/raster/image ops, classify elements, delete, clear
4. `device` — list, activate, status, home, move
5. `export` — render SVG/PNG/DXF via the real backend
6. `console` — pass-through to the raw kernel console (escape hatch)
7. `session` — undo, redo, history, status
8. `repl` — interactive stateful shell (default when no subcommand)

## Output Format

- Human-readable by default (tables, colored).
- `--json` flag on every command for agent consumption.