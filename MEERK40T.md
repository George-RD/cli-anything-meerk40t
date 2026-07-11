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
4. `device` — list, status, home, physical-home, move, info, connect, disconnect, detect, check, jog, goto, frame, setup (driver via top-level `--device`/`--port`/`--baud`, or `--machine PROFILE`)
5. `machine` — list profiles (bundled and user, with origin)
6. `export` — render SVG/PNG/DXF via the real backend
7. `console` — pass-through to the raw kernel console (escape hatch)
8. `session` — undo, redo, history, status
9. `repl` — interactive stateful shell (default when no subcommand)

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

Follow in order. Do not skip steps. Each numbered step is a single command; the safety gates stay as prose.

Safety gates: the laser is live once connected. The operator must wear laser-safety glasses, confirm material focus, and keep a fire-safe area before burning. `device frame` and `device check` never fire the beam; only the console passthrough (`console spool`, which drives the live spooler) does.

### 1. Identify the machine - `device detect [--probe]`

Find the port: `ls /dev/cu.usbserial* /dev/cu.usbmodem*`. Ask the operator for brand and model (not detectable from USB or the GRBL banner); the model determines bed size and endstops.

`cli-anything-meerk40t device detect [--probe]`

`device detect` globs serial ports only and never opens one. With `--probe` it opens each candidate port, writes a GRBL wake/status/`$I` sequence, and reports firmware/version/state.

### 2. Establish the work origin (machines without endstops)

This is a safety step, not a command. Never call `device physical-home`. Have the operator power the machine OFF (never hand-move the head while powered; idle steppers may still be energised), park the head near the front-left corner, then power on. The head's position is now (0,0).

### 3. List available profiles - `machine list`

`cli-anything-meerk40t machine list`

Lists bundled and user profiles with their origin (bundled or user). Use this to pick a profile name for the next step.

### 4. Choose the driver or machine profile - `--machine PROFILE`

Load a profile that sets driver, baud, and bed in one step:

`cli-anything-meerk40t --machine sculpfun-s9 --port /dev/cu.usbserial-10 ...`

### 5. Connect - `device connect`

Confirm with the operator (laser glasses available, bed clear of flammables, gantry unobstructed), then start the persistent REPL (setup) and open the live connection inside it:

```bash
cli-anything-meerk40t --machine sculpfun-s9 --port /dev/cu.usbserial-10
# Inside the REPL:
device connect
```

Expect a controller reset click. Read `$N` (confirm startup blocks are empty) and `$$` (confirm `$32=1`, laser mode; record `$130`/`$131` bed travel and `$30` max S).

### 6. Validate motion - `device jog DX DY [--feed]`

The beam physically cannot fire during `$J=` moves. Coordinates are MACHINE mm, origin front-left, +Y away.

`cli-anything-meerk40t device jog 10 10 --feed 600`

Operator confirms distance and direction (+X right, +Y away).

### 7. Move to a point - `device goto X Y [--feed]`

`cli-anything-meerk40t device goto 0 0 --feed 600`

Operator confirms a clean return to origin.

### 8. Dry-frame the burn area - `device frame X Y W H [--feed]`

`cli-anything-meerk40t device frame X Y W H --feed 1500`

Traces the rectangle corners with the laser off so placement can be checked.

### 9. Preflight the job - `device check`

`cli-anything-meerk40t device check`

It connects, reads `$$`/`$N`, and verifies `$32` (laser mode) and that the `$N` startup blocks are empty. It reports bed travel (`$130`/`$131`) and max S (`$30`) but does NOT verify them against the loaded job, and does not examine job power, feed, or placement. It does not burn; `check()` does not disconnect, so run it in the REPL if the connection must persist. Set every operation's power and speed with `operations set` before burning, and never export with defaults because the auto-created operation is 100% power.

### 10. Capture the profile (optional) - `device setup --save-profile NAME`

`cli-anything-meerk40t device setup --save-profile NAME`

Reads live `$$` settings from the connected GRBL device and writes a user profile (bed size from `$130`/`$131`, firmware in provenance).

### 11. Burn - console passthrough

Operator wears laser glasses; material placed and focused. Re-frame at the burn location for final placement confirmation. Start conservative (15% power) and increase, never the reverse.

There is no dedicated burn subcommand. Run the job through the console passthrough, which drives the live spooler directly. Build the default plan and spool it:

`cli-anything-meerk40t console 'plan default copy preprocess blob spool'`

(The meerk40t `spool` command runs a prepared plan on the live device.)
## GUI-visible operation (operator watches, agent controls)

Use when the operator wants to see the job on the MeerK40t canvas and own
the GUI stop/pause buttons while the agent drives everything remotely.
Backed by the cli_anything bridge plugin, which registers through the
`meerk40t.extension` entry point on `pip install`. The plugin back-fills
three upstream fixes from MeerK40t pull request #3249 at kernel boot when a
stock PyPI install lacks them: handover resolved at execution time, typed
`set` values, and `set` feedback. A frozen application build that bundles a
fixed MeerK40t is unaffected, and once the upstream release ships the fixes
permanently the plugin becomes a no-op.

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
## Community machine profiles

Operators can contribute verified machine profiles to the shared collection
without writing code. Submission is a CLI command plus a GitHub automation
that opens a pull request.

### Command: `profile submit NAME`

A top-level `profile` group was chosen over extending the `machine` group:
`machine` is for selecting a profile to *use* (offline and serial commands),
whereas `profile submit` is about *contributing* a profile, so the two
concerns stay separate.

`profile submit NAME` loads the named profile (user or bundled), validates it
against the community schema, then either opens a pull request (when the `gh`
CLI is installed and authenticated and `--yes` is given) or prints the
profile JSON and a pre-filled GitHub new-issue URL.

Nothing is submitted without an explicit `--yes` flag. Without it the command
prints what would be sent and the exact command to confirm, leaving consent
with the human. No secrets or tokens are handled client-side; the only
network action is the operator's own authenticated `gh` session.

```bash
# Print the plan only; nothing is sent.
cli-anything-meerk40t profile submit sculpfun-s9

# Actually open a pull request (requires gh installed and authenticated).
cli-anything-meerk40t profile submit sculpfun-s9 --yes
```

### Schema and quality bar

Required keys: `name`, `device`, `baud`, `bedwidth`, `bedheight`,
`has_endstops`, `notes`, `provenance`. The `provenance` object carries
`firmware` and `verified`. Every value must be measured, not guessed: bed
size from `device setup --save-profile` (live `$$` readback), firmware from
the `$I` banner, and `has_endstops` from the device state machine. See
`profiles/community/README.md` for the full quality bar.

### Automation

- `.github/ISSUE_TEMPLATE/machine-profile.md`: issue template that asks the
  reporter to paste the profile JSON inside a ```json block.
- `.github/workflows/profile-to-pr.yml`: on an issue labelled
  `community-profile`, validates the JSON block and opens a pull request
  adding `profiles/community/<name>.json`. Invalid JSON is reported back as
  a comment on the issue explaining what to fix.

The `profile submit` CLI validation and the workflow validation enforce the
same schema, so a profile accepted by one is accepted by the other.
