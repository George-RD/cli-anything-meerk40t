---
name: "cli-anything-meerk40t"
description: "Agent CLI harness for MeerK40t laser cutting software — headless project/element/operation/device/export commands with --json output"
---

# cli-anything-meerk40t

Agent CLI harness for **MeerK40t** laser cutting/engraving software. This is a stateful CLI + REPL that wraps the real MeerK40t kernel for headless, agent-driven laser job preparation.

## Prerequisites

- MeerK40t: `pip install -e .` from meerk40t source (or `pip install meerk40t`)
- This CLI: `cd agent-harness && pip install -e .`
- Verify: `cli-anything-meerk40t --help`

## Command Syntax

```bash
cli-anything-meerk40t [--json] [--project PATH] [--session PATH] [--dry-run] [--device DRIVER] [--port PORT] [--baud BAUD] COMMAND [ARGS]
```

- `--json`: Output results as JSON for machine parsing.
- `--project PATH` / `-p PATH`: SVG project file to open and auto-save after mutations.
- `--session PATH` / `-s PATH`: Session file for undo/redo and history.
- `--dry-run`: Print the command that would be executed without applying it.
- `--device DRIVER`: Device driver to activate: `dummy` (default), `grbl`, `lihuiyu`, `moshi`, `ruida`, `newly`, `balor`.
- `--port PORT`: Serial port for the device, e.g. `/dev/cu.usbserial-10`.
- `--baud BAUD`: Baud rate for the serial device (default 115200).
- No subcommand starts the interactive REPL.

## Command Groups

### `project` — SVG project management

Create, open, save, inspect, and close SVG projects.

- `project new [--name NAME]` — Create a new empty project.
- `project open PATH` — Open an existing SVG project.
- `project save PATH [--version VERSION]` — Save the current project to an SVG file.
- `project info` — Show project metadata.
- `project close` — Close the current project.

### `elements` — Shape and object manipulation

Add geometric primitives and text, list, select, delete, clear, transform, align, and group/ungroup elements.

- `elements circle CX CY R [--stroke COLOR] [--fill COLOR]` — Add a circle.
- `elements rect X Y W H [--stroke COLOR] [--fill COLOR]` — Add a rectangle.
- `elements ellipse CX CY RX RY [--stroke COLOR] [--fill COLOR]` — Add an ellipse.
- `elements line X1 Y1 X2 Y2 [--stroke COLOR]` — Add a line.
- `elements polyline X1 Y1 X2 Y2 ... [--stroke COLOR]` — Add a polyline from coordinate pairs.
- `elements text X Y TEXT [--stroke COLOR] [--fill COLOR]` — Add a text element.
- `elements list` — List all elements in the project.
- `elements delete INDEX` — Delete the element at the given index.
- `elements select INDEX` — Select an element by index.
- `elements clear` — Remove all elements.
- `elements frame` — Add a frame element around the project bounds.
- `elements translate INDEX DX DY [--absolute]` — Translate an element by an offset (or to absolute coordinates). Units are supported (e.g., `10mm`, `2in`).
- `elements scale INDEX FACTOR` — Scale an element by a numeric factor.
- `elements rotate INDEX ANGLE` — Rotate an element by an angle (e.g., `90deg`, `1.57rad`).
- `elements align MODE` — Align selected elements. Modes: `left`, `right`, `top`, `bottom`, `center`, `centerh`, `centerv`.
- `elements group [-l LABEL]` — Group selected elements together with an optional label.
- `elements ungroup` — Ungroup the currently selected group elements.

### `operations` — Laser operation management

Add, classify, configure, delete, and clear operations that map elements to laser actions.

- `operations list` — List operations.
- `operations add TYPE` — Add an operation: `cut`, `engrave`, `raster`, `image`, or `dots`.
- `operations classify` — Classify elements into operations.
- `operations declassify` — Remove element-to-operation assignments.
- `operations set INDEX KEY VALUE` — Set a property on an operation by index.
- `operations delete INDEX` — Delete the operation at the given index.
- `operations clear` — Remove all operations.

### `device` — Device control and status

Inspect and control laser devices.

- `device list` — List available devices.
- `device status` — Show current device status (position and connection state).
- `device home` — Home the device.
- `device physical-home` — Perform physical home.
- `device move X Y [--absolute/--relative]` — Move the device to/by coordinates.
- `device info` — Show device information.
- `device connect` — Open the active device's controller/transport connection (e.g. GRBL `controller.open()`).
- `device disconnect` — Close the active device's controller/transport connection (`controller.close()`).

### `export` — Output formats

Export the project to several formats. Not all formats work headless.

- `export svg PATH [--version VERSION]` — Export as SVG (headless, truthful).
- `export svgz PATH` — Export as compressed SVGZ.
- `export png PATH [--dpi DPI]` — Export as PNG (requires wxPython GUI renderer).
- `export gcode PATH` — Export as G-code (requires an active GRBL device).

### `console` — Raw passthrough

Send a raw command directly to the MeerK40t console.

- `console 'COMMAND'` — Execute any MeerK40t console command. Example: `console 'circle 2in 2in 1in'`.

### `session` — Undo, redo, history, and status

Manage the mutable session state.

- `session undo` — Undo the last command.
- `session redo` — Redo the last undone command.
- `session history` — Show command history.
- `session status` — Show session status.

### `repl` — Interactive shell

- `repl` — Start the interactive REPL. This is also the default behavior when no subcommand is provided.

## Agent Guidance

- Use `--json` for machine-readable output on every command.
- Use `--project PATH` to persist mutations back to an SVG file across invocations.
- Use `console '...'` as an escape hatch for any MeerK40t console command not exposed directly.
- Export formats: SVG is fully headless; SVGZ is also fully headless; G-code requires an active GRBL device (`console 'service device start -i grbl'`); PNG requires a wxPython GUI environment.
- For real hardware, activate the driver with `--device grbl --port /dev/cu.usbserial-10` (or `lihuiyu`, etc.), then open the link with `device connect` and close it with `device disconnect`. Run these inside the REPL so the connection persists: each one-shot command boots a fresh backend and shuts it down on exit. `device status` reports `connected`, `port`, and `baud` without touching the port.
- Typical workflow: `project new` → add `elements` → `operations classify` → `export svg`.

## Safety and placement traps (field-verified on real hardware)

- **Default operation power is 100%** (`power=1000`). Set power and speed explicitly before any export or burn: `operations set 0 power 150` is 15% when GRBL `$30=1000`.
- **Bed size drives Y placement.** G-code Y is flipped through the device bed height; a fresh kernel defaults to 235mm. Set the machine's real bed size (GRBL `$130`/`$131`) via `console 'set bedwidth 410mm'` / `'set bedheight 400mm'` and refresh the view, then verify the exported G-code Y range matches the intended location before sending. A wrong bed size misplaces the burn silently.
- **Export G-code from a disconnected kernel.** The plan pipeline (`plan copy preprocess validate blob save_job`) blocks indefinitely when the device holds an open serial connection.
- **Never `device physical-home` on machines without limit switches** (most diode engravers). The work origin is wherever the head sits at power-on; park it near the front-left corner with the machine powered off, then power on.
- **Verify before motion:** after connecting, check GRBL `$32=1` (laser mode keeps the beam off during positioning) and that `$N` startup blocks are empty. Validate motion with `$J=` jogs (cannot fire the laser) before running any job.

## Examples

### 1. Create a laser job, add shapes, classify, and export SVG

```bash
cli-anything-meerk40t --json project new --name my-job
cli-anything-meerk40t --json --project my-job.svg elements circle 1in 1in 1in --stroke red --fill none
cli-anything-meerk40t --json --project my-job.svg elements rect 2in 2in 1in 1in --stroke red --fill blue
cli-anything-meerk40t --json --project my-job.svg operations classify
cli-anything-meerk40t --json --project my-job.svg export svg my-job-out.svg
```

### 2. G-code generation with the GRBL device

```bash
cli-anything-meerk40t --project job.svg operations classify
cli-anything-meerk40t --project job.svg console 'service device start -i grbl'
cli-anything-meerk40t --project job.svg export gcode job.gcode
```

### 3. Inspect a project with JSON output

```bash
cli-anything-meerk40t --json --project job.svg elements list
cli-anything-meerk40t --json --project job.svg operations list
cli-anything-meerk40t --json --project job.svg project info
```

### 4. Console passthrough to home the device

```bash
cli-anything-meerk40t console 'home'
```

### 5. Session management with undo and history

```bash
cli-anything-meerk40t --json --session session.json session status
cli-anything-meerk40t --json --session session.json session undo
cli-anything-meerk40t --json --session session.json session history
```

### 6. Drive a real GRBL diode laser

Find the serial port, then start a persistent REPL session so the connection stays open across commands:

```bash
ls /dev/cu.usbserial*            # discover the port on macOS
cli-anything-meerk40t --device grbl --port /dev/cu.usbserial-10
# Inside the REPL:
device status     # shows port/baud, connected=false
device connect    # opens the serial connection
device status     # connected=true
device disconnect # closes the serial connection
exit
```

Each one-shot `cli-anything-meerk40t ...` invocation boots a fresh backend and shuts it down on exit, so `device connect` in a one-shot command opens the link and then closes it when the process ends. Use the REPL to keep a connection alive across multiple commands. `device connect`/`device disconnect` call the active device's `controller.open()`/`controller.close()` directly; there is no `connect` console command in MeerK40t. The dummy device has no connectable controller, so `device connect` returns an error shape rather than touching any port.
