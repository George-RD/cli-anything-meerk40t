# cli-anything-meerk40t

![CLI-Anything ecosystem](https://img.shields.io/badge/CLI--Anything-ecosystem-blue)

Agent CLI harness for MeerK40t laser software. It is built so an LLM, or a
human at a terminal, can take a design from SVG to a physically engraved
object through the real MeerK40t kernel.

Docs: [cli-anything-meerk40t skill](skills/cli-anything-meerk40t/SKILL.md)
(a router covering command usage, hardware safety, and material settings via
its [references](skills/cli-anything-meerk40t/references/)), and the
[MeerK40t SOP](MEERK40T.md).

## Install

```bash
pip install cli-anything-meerk40t

# Or install the agent skill with npx:
npx skills add George-RD/cli-anything-meerk40t --skill cli-anything-meerk40t -g -y
```

MeerK40t and all headless dependencies are installed automatically.

## Quick start

```bash
# 1. Detect the machine and read the port
cli-anything-meerk40t device detect --probe

# 2. Preflight the GRBL device against the Sculpfun S9 profile
cli-anything-meerk40t --machine sculpfun-s9 --port /dev/cu.usbserial-10 device check

# 3. Build the job: add a rectangle, classify it, set power and speed
cli-anything-meerk40t --json project new --project /tmp/job.svg
cli-anything-meerk40t --json --project /tmp/job.svg elements rect 80mm 50mm 10mm 10mm
cli-anything-meerk40t --json --project /tmp/job.svg operations classify
cli-anything-meerk40t --json --project /tmp/job.svg operations set 0 power 150
cli-anything-meerk40t --json --project /tmp/job.svg operations set 0 speed 25

# 4. Frame the placement. OPERATOR MUST BE PRESENT: the beam is live once connected.
cli-anything-meerk40t --machine sculpfun-s9 --port /dev/cu.usbserial-10 device frame 10 10 80 50 --feed 1500

# 5. Export G-code. Burning needs the operator present; read the skill's references/hardware.md first.
cli-anything-meerk40t --device grbl --machine sculpfun-s9 --project /tmp/job.svg export gcode /tmp/job.gcode
```

The export prints a placement summary: the bounding box, the bed origin, and
the Y-flip applied. Check it before the operator runs the job.

## What it does

A stateful CLI + REPL that wraps the **real MeerK40t kernel** for headless,
agent-driven laser job preparation. It exposes project, elements, operations,
device, export, session, and console-passthrough commands with `--json` output
for AI agents.

Two skills ship with this harness:

- [`cli-anything-meerk40t`](skills/cli-anything-meerk40t/SKILL.md): the single
  skill, structured as a router. Its [references](skills/cli-anything-meerk40t/references/)
  hold the depth: [commands.md](skills/cli-anything-meerk40t/references/commands.md)
  (CLI usage and JSON contracts), [hardware.md](skills/cli-anything-meerk40t/references/hardware.md)
  (safety gates, GRBL preflight, coordinates, failure diagnosis), and
  [materials.md](skills/cli-anything-meerk40t/references/materials.md)
  (power and speed method plus field-verified data).

## Command groups

| Group | Description |
|---|---|
| `project` | new, open, save, info, close (SVG project files) |
| `elements` | circle, rect, ellipse, line, polyline, text, list, delete, select, clear, frame |
| `operations` | list, add (cut/engrave/raster/image/dots), classify, declassify, set |
| `device` | list, status, home, physical-home, move, info, connect, disconnect, detect, check, jog, goto, frame, setup |
| `machine` | list profiles (bundled and user, with origin) |
| `profile` | submit NAME - contribute a community machine profile |
| `export` | svg, svgz (real backend); png (GUI-dependent); gcode (GRBL device required) |
| `console` | Raw passthrough to the MeerK40t kernel console |
| `session` | undo, redo, history, status |
| `repl` | Interactive shell (default) |

## Driving real hardware

The driver is selected with top-level options before any subcommand:

```bash
cli-anything-meerk40t --device grbl --port /dev/cu.usbserial-10 --baud 115200
```

Supported drivers: `dummy` (default, no hardware), `grbl`, `lihuiyu`,
`moshi`, `ruida`, `newly`, `balor`. Load a bundled or user machine
profile with `--machine PROFILE`; it sets the driver, baud, and bed size. The
port is only needed for serial commands (connect, check, jog, goto, frame,
setup); offline commands (export, elements, machine list, project ops) work
without one.
List profiles with `machine list`. Start a REPL so the
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

Safety gates, origin discipline, power and speed, GRBL preflight, coordinates,
and failure diagnosis live in the skill's
[references/hardware.md](skills/cli-anything-meerk40t/references/hardware.md) and
[references/materials.md](skills/cli-anything-meerk40t/references/materials.md).

## Export formats

- **SVG** (default, plain, compressed/svgz): truthful, rendered by the real MeerK40t SVGWriter. Works headless.
- **G-code**: generated via the real GRBL `save_job` pipeline. Requires an active GRBL device; select it with `--device grbl` on the export command (each one-shot command boots a fresh backend, so a separate `console 'service device start -i grbl'` does not carry over).
- **PNG**: requires wxPython GUI. Errors clearly in headless mode.

## Status

v1.3.0. 108 unit tests and 13 E2E tests pass, 100% pass rate.

Live-verified against a Sculpfun S9 (GRBL 1.1h, CH340) on macOS.

Out of scope: GUI replacement, non-GRBL hardware so far, and camera support.

## Testing

```bash
pip install -e .
python -m unittest cli_anything.meerk40t.tests.test_core -v
python -m unittest cli_anything.meerk40t.tests.test_full_e2e -v
```

## License

MIT

---

Part of the CLI-Anything (https://github.com/HKUDS/CLI-Anything) ecosystem,
built with its `/cli-anything` pipeline and listed on the CLI-Hub.
