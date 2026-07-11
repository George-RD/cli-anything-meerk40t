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

## Hardware workflow (GRBL serial machines)

Follow in order. Do not skip steps.

### 1. Identify the machine

1. Find the port: `ls /dev/cu.usbserial* /dev/cu.usbmodem*`.
2. Ask the operator for brand and model (not detectable from USB or the
   GRBL banner). The model determines bed size and endstops.

### 2. Establish the work origin (machines without endstops)

1. Never call `device physical-home`.
2. Have the operator power the machine OFF (never hand-move the head while
   powered; idle steppers may still be energised).
3. Park the head near the front-left corner, not pressed into the frame.
4. Power on. The head's position is now (0,0).

### 3. Connect and preflight

1. Confirm with the operator: laser glasses available, bed clear of
   flammables, gantry unobstructed.
2. `device connect` (in the REPL; expect a controller reset click).
3. Read `$N`: confirm startup blocks are empty.
4. Read `$$`: confirm `$32=1` (laser mode: beam off during positioning).
5. Record `$130`/`$131` (true bed travel) and `$30` (max S value).

### 4. Validate motion (beam physically cannot fire during `$J=` jogs)

1. Jog 10mm: `$J=G91 X10 Y10 F600`. Operator confirms distance and
   direction (+X right, +Y away from operator).
2. Jog to bed centre and back to 0,0. Operator confirms clean return.
3. Dry-frame the intended burn area with absolute jogs.

### 5. Prepare the job

1. Set bed size from `$130`/`$131`: `console 'set bedwidth 410mm'`,
   `console 'set bedheight 400mm'`, then refresh the view (`dev.realize()`
   via the backend; console `set` alone does not refresh).
2. Set power and speed on every operation before export:
   `operations set 0 power 150` (S value; 150/$30=15%),
   `operations set 0 speed 25` (mm/s). Never export with defaults: the
   auto-created op is 100% power.
3. Export G-code with the device DISCONNECTED (the plan pipeline blocks on
   a live serial link).
4. Verify the exported X/Y ranges match the dry-framed area before sending.

### 6. Burn

1. Operator wears laser glasses; material placed and focused.
2. Re-frame at the burn location for final placement confirmation.
3. Start conservative (15% power) and increase, never the reverse.

## GUI-visible operation (operator watches, agent controls)

Use when the operator wants to see the job on the MeerK40t canvas and own
the GUI stop/pause buttons while the agent drives everything remotely.
Requires the patched MeerK40t (branch `fix_console_server_handover`):
handover resolved at execution time, typed `set` values, `set` feedback.

1. Write a boot batch file: line 1 `consoleserver -p 2323`, line 2
   `load /path/job.svg`. Launch `meerk40t -b bootfile`.
2. Connect over TCP (telnet-style, one command per connection works).
   With the patch, element commands are safe; unpatched, NEVER send
   element-mutating commands remotely (GUI segfault).
3. Configure the device with an explicit context path:
   `set -p grbl serial_port /dev/cu.usbserial-10`, `set -p grbl baud_rate
   115200`, `set -p grbl bedwidth 410mm`, `set -p grbl bedheight 400mm`.
   Bare `set` targets the root context and does nothing to the device.
4. `flush` after config. Bed-size changes need a GUI restart to rebuild
   the coordinate view (the realize signal cannot be triggered remotely).
5. Open the serial link by sending any G-code query: `gcode $I` (the
   controller opens the port on demand). Verify with the operator that
   the machine responded; queue echo alone is not proof.
6. Motion the operator should see: use driver commands (`move_absolute
   0mm 400mm`) or spooled jobs — these update the canvas position dot.
   Raw `gcode G0 ...` moves the machine but is INVISIBLE to the GUI;
   reserve raw `gcode` for GRBL housekeeping (`$` queries, resets).
7. Coordinate conventions: canvas ruler 0 is top-left (design space);
   the machine position dot sits at scene bottom-left = machine (0,0) =
   front-left of the physical machine. Flip Y maps between them.
