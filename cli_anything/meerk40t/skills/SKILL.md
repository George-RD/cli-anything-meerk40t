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
cli-anything-meerk40t [--json] [--project PATH] [--session PATH] [--dry-run] COMMAND [ARGS]
```

- `--json`: Output results as JSON for machine parsing.
- `--project PATH` / `-p PATH`: SVG project file to open and auto-save after mutations.
- `--session PATH` / `-s PATH`: Session file for undo/redo and history.
- `--dry-run`: Print the command that would be executed without applying it.
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

Add geometric primitives and text, list, select, delete, or clear elements.

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

### `operations` — Laser operation management

Add, classify, and configure operations that map elements to laser actions.

- `operations list` — List operations.
- `operations add TYPE` — Add an operation: `cut`, `engrave`, `raster`, `image`, or `dots`.
- `operations classify` — Classify elements into operations.
- `operations declassify` — Remove element-to-operation assignments.
- `operations set INDEX KEY VALUE` — Set a property on an operation by index.

### `device` — Device control and status

Inspect and control laser devices.

- `device list` — List available devices.
- `device status` — Show current device status.
- `device home` — Home the device.
- `device physical-home` — Perform physical home.
- `device move X Y [--absolute/--relative]` — Move the device to/by coordinates.
- `device info` — Show device information.

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
- Typical workflow: `project new` → add `elements` → `operations classify` → `export svg`.

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
