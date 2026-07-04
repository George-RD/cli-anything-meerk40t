# cli-anything-meerk40t

Agent CLI harness for **MeerK40t** laser cutting/engraving software.

Part of the [CLI-Anything](https://github.com/HKUDS/CLI-Anything) ecosystem —
install via `cli-hub install meerk40t` or `pip install cli-anything-meerk40t`.

## What it does

A stateful CLI + REPL that wraps the **real MeerK40t kernel** for headless,
agent-driven laser job preparation. It exposes project, elements, operations,
device, export, session, and console-passthrough commands with `--json` output
for AI agents.

## Install

```bash
# Install the CLI harness
pip install cli-anything-meerk40t

# Verify
cli-anything-meerk40t --help
```

MeerK40t and all headless dependencies are installed automatically.

## Usage

```bash
# One-shot commands
cli-anything-meerk40t --json project new
cli-anything-meerk40t --json -p /tmp/job.svg elements circle 1in 1in 1in
cli-anything-meerk40t --json -p /tmp/job.svg elements rect 2in 2in 1in 1in --stroke red --fill blue
cli-anything-meerk40t --json -p /tmp/job.svg elements list
cli-anything-meerk40t --json operations classify
cli-anything-meerk40t --json export svg /tmp/out.svg

# Console passthrough (escape hatch to raw MeerK40t console)
cli-anything-meerk40t console 'service device start -i grbl'
cli-anything-meerk40t --json export gcode /tmp/out.gcode

# Interactive REPL (default when no subcommand)
cli-anything-meerk40t
```

## Command groups

| Group | Description |
|---|---|
| `project` | new, open, save, info, close (SVG project files) |
| `elements` | circle, rect, ellipse, line, polyline, text, list, delete, select, clear, frame |
| `operations` | list, add (cut/engrave/raster/image/dots), classify, declassify, set |
| `device` | list, status, home, physical-home, move, info |
| `export` | svg, svgz (real backend); png (GUI-dependent); gcode (GRBL device required) |
| `console` | Raw passthrough to the MeerK40t kernel console |
| `session` | undo, redo, history, status |
| `repl` | Interactive shell (default) |

## Export formats

- **SVG** (default, plain, compressed/svgz) — truthful, rendered by the real MeerK40t SVGWriter. Works headless.
- **G-code** — generated via the real GRBL `save_job` pipeline. Requires an active GRBL device.
- **PNG** — requires wxPython GUI. Errors clearly in headless mode.

## Testing

```bash
pip install -e .
python -m unittest cli_anything.meerk40t.tests.test_core -v
python -m unittest cli_anything.meerk40t.tests.test_full_e2e -v
```

38 tests, 100% pass rate.

## License

MIT