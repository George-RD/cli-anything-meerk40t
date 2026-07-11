# MeerK40t — Agent CLI Harness SOP

## Software Overview

MeerK40t is a plugin-based laser cutting/engraving control application built around a
**Kernel** service bus. The kernel provides a console command system, signals,
channels, services, and a plugin lifecycle.

- **Backend engine**: `meerk40t.kernel.Kernel` — central service bus with a console
  parser, signal system, channel messaging, and a plugin lifecycle.
- **Native format**: SVG (load/save via `meerk40t/core/svg_io.py`). DXF is also
  supported via `meerk40t/dxf/`. The elements tree is the project state.
- **Device drivers**: `dummy` (headless), `grbl`, `lihuiyu`, `ruida`, `moshi`, `newly`, `balor`.
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
4. `device` — list, status, home, move, connect, disconnect (driver via top-level `--device`/`--port`/`--baud`)
5. `export` — render SVG/PNG/DXF via the real backend
6. `console` — pass-through to the raw kernel console (escape hatch)
7. `session` — undo, redo, history, status
8. `repl` — interactive stateful shell (default when no subcommand)

## Output Format

- Human-readable by default (tables, colored).
- `--json` flag on every command for agent consumption.

## Real hardware

The CLI selects the device driver with top-level options passed before any
subcommand: `--device DRIVER` (default `dummy`), `--port TEXT`, and
`--baud INTEGER` (default `115200`). The backend starts the requested driver
via `service device start -i <driver> 0` and wires `serial_port`/`baud_rate`
on `kernel.device` for serial drivers (e.g. GRBL).

Open and close the link with `device connect` and `device disconnect`. These
call the active device's `controller.open()`/`controller.close()` directly;
MeerK40t has no `connect` console command. The dummy device has no connectable
controller, so `device connect` returns an error shape rather than touching a
port. Because each one-shot command boots a fresh backend and shuts it down on
exit, run `connect`/`status`/`disconnect` inside the REPL to keep the link
alive across commands.

## Field-verified traps (Sculpfun S9 bring-up, 2026-07-11)

These were found driving a real GRBL 1.1h machine and will bite any agent:

1. **Default operation power is 100%.** The kernel auto-creates an engrave op
   with `power=1000`, `speed=20`. Always set power and speed explicitly
   (`operations set 0 power 150` = 15% when GRBL `$30=1000`) before export.
2. **Y placement depends on bed size.** G-code Y is flipped through the device
   bed height. A fresh kernel defaults to a 235mm bed; on a 410x400mm machine
   the burn lands ~165mm off. Set `bedwidth`/`bedheight` to the machine's
   `$130`/`$131` values AND call `dev.realize()` (a console `set bedheight`
   alone does not refresh the coordinate view). Always check the exported
   G-code Y range against the framed area before sending.
3. **`plan ... save_job` hangs with a live connection.** The plan pipeline
   blocks indefinitely in a kernel whose device holds an open serial link.
   The file is written correctly first and nothing is sent, but export
   G-code from a disconnected kernel.
4. **No-endstop machines (most diode engravers):** never call
   `physical-home`. Work origin is wherever the head sits at power-on. Park
   the head near the front-left corner with the machine POWERED OFF (idle
   steppers may still be energised; never back-drive a locked gantry), then
   power on. Trust `$130`/`$131` for travel limits over the vendor spec sheet.
5. **First-connect safety:** a GRBL reset executes stored `$N` startup
   blocks. After connecting, read `$N` and `$$` and verify `$32=1` (laser
   mode, beam off during G0/jog) before authorising any motion. Validate
   motion with `$J=` jogs (cannot fire the laser): 10mm jog, centre and
   back, then a dry frame of the burn area.
6. **Brand/model is not detectable** from USB or the GRBL banner (generic
   CH340 + `Grbl 1.1h`). Ask the operator; the model determines bed size and
   endstops.
