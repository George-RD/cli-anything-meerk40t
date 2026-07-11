# cli-anything-meerk40t

Agent CLI harness for **MeerK40t** laser software. It is built so an LLM, or a human at a terminal, can take a design from SVG to a physically engraved object through the real MeerK40t kernel.

This is a stateful CLI + REPL that wraps the **real MeerK40t kernel** for
headless, agent-driven laser job preparation. It exposes project, elements,
operations, device, export, session, and console-passthrough commands with
`--json` output for AI agents.

## Install

### 1. Install MeerK40t (the real software, a hard dependency)

```bash
# From source (recommended: pulls all headless deps):
git clone https://github.com/meerk40t/meerk40t
cd meerk40t
pip install -r requirements-nogui.txt
pip install -e .

# Or from PyPI:
pip install meerk40t
```

Headless dependencies: `numpy`, `pyusb`, `pyserial`, `Pillow`, `ezdxf`,
`requests`, `websocket-client`.

### 2. Install this CLI harness

```bash
cd agent-harness
pip install -e .
```

Verify:
```bash
cli-anything-meerk40t --help
```

## Usage

### One-shot commands

```bash
# Create a new project and add elements
cli-anything-meerk40t project new
cli-anything-meerk40t elements circle 1in 1in 1in
cli-anything-meerk40t elements rect 2in 2in 1in 1in --stroke red --fill blue
cli-anything-meerk40t elements text 3in 3in "Hello"

# Persist to an SVG file (auto-saves after each mutation when -p is given)
cli-anything-meerk40t -p /tmp/job.svg elements circle 1in 1in 1in
cli-anything-meerk40t -p /tmp/job.svg elements list

# Operations
cli-anything-meerk40t operations list
cli-anything-meerk40t operations add cut
cli-anything-meerk40t operations classify
cli-anything-meerk40t operations delete 0
cli-anything-meerk40t operations clear

# Transformations, Alignment, and Grouping
cli-anything-meerk40t -p /tmp/job.svg elements translate 0 10mm 20mm
cli-anything-meerk40t -p /tmp/job.svg elements scale 1 2.0
cli-anything-meerk40t -p /tmp/job.svg elements rotate 0 90deg
cli-anything-meerk40t -p /tmp/job.svg elements align center
cli-anything-meerk40t -p /tmp/job.svg elements group -l MyGroup
cli-anything-meerk40t -p /tmp/job.svg elements ungroup

# Export via the real backend
cli-anything-meerk40t export svg /tmp/out.svg
cli-anything-meerk40t export svgz /tmp/out.svgz

# Device control
cli-anything-meerk40t device status
cli-anything-meerk40t device home

# Console passthrough (escape hatch to the raw MeerK40t console)
cli-anything-meerk40t console 'circle 2in 2in 1in'
cli-anything-meerk40t console 'service device start -i grbl'
```

### JSON output (for agents)

```bash
cli-anything-meerk40t --json elements circle 1in 1in 1in
cli-anything-meerk40t --json elements list
cli-anything-meerk40t --json export svg /tmp/out.svg
```

### REPL (default when no subcommand)

```bash
cli-anything-meerk40t
# Enter interactive REPL with banner, history, and help
```

### Session management

```bash
cli-anything-meerk40t -s /tmp/session.json session status
cli-anything-meerk40t -s /tmp/session.json session undo
```

## Command groups

| Group | Description |
|---|---|
| `project` | New, open, save, info, close (SVG project files) |
| `elements` | Circle, rect, ellipse, line, polyline, text, list, delete, select, clear, frame, translate, scale, rotate, align, group, ungroup |
| `operations` | List, add (cut/engrave/raster/image/dots), classify, declassify, set, delete, clear |
| `device` | List, status, home, physical-home, move, info, connect, disconnect, detect, check, jog, goto, frame, setup |
| `machine` | List profiles (bundled and user, with origin) |
| `export` | SVG, SVGZ (real backend); PNG (GUI-dependent); G-code (GRBL device required) |
| `console` | Raw passthrough to the MeerK40t kernel console |
| `session` | Undo, redo, history, status |
| `repl` | Interactive shell (default) |

## Driving real hardware

The driver is selected with top-level options before any subcommand:

```bash
cli-anything-meerk40t --device grbl --port /dev/cu.usbserial-10 --baud 115200
```

Supported drivers: `dummy` (default, no hardware), `grbl`, `lihuiyu`,
`moshi`, `ruida`, `newly`, `balor`. Load a bundled or user machine
profile with `--machine PROFILE` (requires `--port`); it sets the driver,
baud, and bed size. List profiles with `machine list`. Start a REPL so the
connection to the device controller persists across commands:

```bash
cli-anything-meerk40t --device grbl --port /dev/cu.usbserial-10
# Inside the REPL:
device status     # port/baud, connected=false
device connect    # opens the controller/transport connection
device status     # connected=true
device disconnect # closes the connection
```

`device connect`/`device disconnect` call the active device's
`controller.open()`/`controller.close()`; there is no `connect` console
command in MeerK40t. The dummy device has no connectable controller, so
`device connect` returns an error shape rather than touching any port. Each
one-shot command boots a fresh backend and shuts it down on exit, so keep a
session open in the REPL to maintain the link.

## Export formats

- **SVG** (default, plain, compressed/svgz): truthful, rendered by the real
  MeerK40t SVGWriter. Works headless.
- **G-code**: generated via the real GRBL `save_job` pipeline. Requires an
  active GRBL device (`console 'service device start -i grbl'`).
- **PNG**: requires wxPython GUI (`render-op/make_raster` is only registered
  by the GUI plugin). Errors clearly in headless mode.

## How it works

The harness boots a headless MeerK40t `Kernel` instance (the same code path as
`meerk40t -z`) and drives it via `kernel.console()`. Channel output is captured
via `_console_channel.watch()`. All element/operation/export commands are
translated to real MeerK40t console commands; this is a wrapper, not a
reimplementation.

## Testing

```bash
cd agent-harness
CLI_ANYTHING_FORCE_INSTALLED=1 python -m pytest cli_anything/meerk40t/tests/ -v -s
```

See `tests/TEST.md` for the test plan and results.