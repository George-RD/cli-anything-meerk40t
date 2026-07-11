# CLI command reference

*Full syntax for cli-anything-meerk40t. For safety, origin, and preflight meaning read [hardware.md](hardware.md); for power/speed read [materials.md](materials.md).*

## Prerequisites

- Python 3.10 or newer
- `pip install cli-anything-meerk40t`
- The real MeerK40t kernel (installed automatically)
- For real hardware: a USB serial port such as `/dev/cu.usbserial-10` and operator laser-safety glasses

## Command syntax

```bash
cli-anything-meerk40t [--json] [--device DRIVER] [--port PORT] [--baud N] [--machine PROFILE] [--project PATH] [--session PATH] GROUP COMMAND [ARGS]
```

- Top-level flags: `--json` (machine-readable output), `--device DRIVER` (dummy, grbl, lihuiyu, moshi, ruida, newly, balor), `--port PORT`, `--baud N`, `--machine PROFILE` (load a bundled or user profile), `--project PATH`, `--session PATH`.
- Every command supports `--json`.
- Run with no subcommand for the interactive REPL.

## Command groups

| Group | Description |
|---|---|
| `project` | new, open, save, info, close (SVG project files) |
| `elements` | circle, rect, ellipse, line, polyline, text, list, delete, select, clear, frame |
| `operations` | list, add (cut/engrave/raster/image/dots), classify, declassify, set |
| `device` | list, status, home, physical-home, move, info, connect, disconnect, detect, check, jog, goto, frame, setup |
| `machine` | list profiles (bundled and user, with origin) |
| `profile` | submit NAME - contribute a community machine profile (validates, then opens a PR via gh or prints an issue URL) |
| `export` | svg, svgz (real backend); png (GUI-dependent); gcode (GRBL device required) |
| `console` | Raw passthrough to the MeerK40t kernel console |
| `session` | undo, redo, history, status |
| `repl` | Interactive shell (default) |

## Project workflow

`cli-anything-meerk40t --json project new`
`cli-anything-meerk40t --json --project /tmp/job.svg elements circle 1in 1in 1in`
`cli-anything-meerk40t --json --project /tmp/job.svg elements rect 2in 2in 1in 1in --stroke red --fill blue`
`cli-anything-meerk40t --json --project /tmp/job.svg elements list`
`cli-anything-meerk40t --json operations classify`
`cli-anything-meerk40t --json export svg /tmp/out.svg`

## Device commands

`device status` - port/baud and `connected` state.
`device detect [--probe]` - list serial ports. `device detect` globs ports only and never opens one; with `--probe` it opens each candidate port and writes a GRBL wake/status/`$I` sequence, reporting firmware/version/state.
`device check` - preflight the GRBL device: it connects, reads `$$`/`$N`, and verifies `$32` (laser mode) and empty `$N` startup blocks. It reports bed travel (`$130`/`$131`) and max S (`$30`) but does NOT verify them, and does not examine job power, feed, or placement. It does not burn; `check()` does not disconnect, so run it in the REPL if the connection must persist.
`device connect` - open the controller/transport connection (REPL).
`device disconnect` - close the connection.
`device home` - home the machine (requires a live connection).
`device physical-home` - home using physical endstops (requires a live connection).
`device jog DX DY [--feed]` - jog by X/Y millimetres (requires a live connection). Coordinates are MACHINE mm, origin front-left, +Y away.
`device goto X Y [--feed]` - move to an absolute point in MACHINE mm (requires a live connection).
`device frame X Y W H [--feed]` - trace the cut bounds with the laser off so placement can be checked (requires a live connection). Coordinates are MACHINE mm, origin front-left, +Y away.
`device setup --save-profile NAME` - read live `$$` settings from a connected GRBL device and write a user profile (bed size from `$130`/`$131`, firmware in provenance).

> The dummy device has no connectable controller, so `device connect` returns an error shape rather than touching any port. `jog`, `goto`, and `frame` refuse without a live connection.

## Machine profiles

`machine list` - show bundled and user profiles with their origin (`bundled` or `user`).
`--machine PROFILE` - load a profile that sets the driver, baud, and bed size in one step. A profile is needed for offline commands (export, elements, machine list, project ops); serial commands (connect, check, jog, goto, frame, setup) also need `--port`. An unknown profile emits a `--json` error listing the available names and exits 1.
User profiles live in `~/.config/cli-anything-meerk40t/profiles/` (override with `CLI_ANYTHING_CONFIG_HOME`); a user profile wins over a bundled one with the same name.

## Community machine profiles

`profile submit NAME` contributes a machine profile to the shared
collection. It loads the named profile, validates it against the community
schema, then either opens a pull request (when the `gh` CLI is installed,
authenticated, and `--yes` is given) or prints the profile JSON and a
pre-filled GitHub new-issue URL.

Nothing is submitted without `--yes`. Without it the command prints what
would be sent and the exact command to confirm, leaving consent with the
human. No secrets or tokens are handled client-side.

```bash
cli-anything-meerk40t profile submit sculpfun-s9        # plan only
cli-anything-meerk40t profile submit sculpfun-s9 --yes  # open a PR
```

A `machine-profile` issue template plus the `profile-to-pr.yml` workflow
let an operator contribute by opening an issue instead; the workflow
validates the JSON block and opens the pull request automatically. The
quality bar (values from live `$$` readback, firmware banner, and the
device state machine) is documented in `profiles/community/README.md`.

## Agent guidance

- Always pass `--json` so the agent can parse structured results.
- `--project PATH` is required for element, operation, and export commands (use `--json project new` to create one).
- The dummy device is for project work only; it cannot burn.
- Each one-shot command boots a fresh backend and shuts it down on exit, so keep a session open in the REPL to maintain a live connection.

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
cli-anything-meerk40t --device grbl --project job.svg operations classify
cli-anything-meerk40t --device grbl --project job.svg export gcode job.gcode
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

Find the serial port and pick a machine profile, then start a persistent REPL session so the connection stays open across commands:

```bash
ls /dev/cu.usbserial*            # discover the port on macOS
cli-anything-meerk40t machine list                       # list bundled/user profiles
cli-anything-meerk40t --machine sculpfun-s9 --port /dev/cu.usbserial-10
# Inside the REPL:
device status     # shows port/baud, connected=false
device connect    # opens the serial connection
device status     # connected=true
device detect     # list ports inside the REPL
device frame 0 0 100 100 --feed 1500   # trace bounds, laser off
device check      # connect + preflight, does not burn
device disconnect # closes the serial connection
exit
```

Each one-shot `cli-anything-meerk40t ...` invocation boots a fresh backend and shuts it down on exit, so `device connect` in a one-shot command opens the link and then closes it when the process ends. Use the REPL to keep a connection alive across multiple commands. `device connect`/`device disconnect` call the active device's `controller.open()`/`controller.close()` directly; there is no `connect` console command in MeerK40t. The dummy device has no connectable controller, so `device connect` returns an error shape rather than touching any port.
