# cli-anything-meerk40t

Agent CLI harness for **MeerK40t** laser software. It is headless, outputs `--json` by default for agents, and wraps the real MeerK40t kernel.

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

## Agent guidance

- Always pass `--json` so the agent can parse structured results.
- `--project PATH` is required for element, operation, and export commands (use `--json project new` to create one).
- The dummy device is for project work only; it cannot burn.
- Each one-shot command boots a fresh backend and shuts it down on exit, so keep a session open in the REPL to maintain a live connection.

## Hardware workflow (follow in order)

Safety gates: the laser is live once connected. The operator must wear laser-safety glasses, confirm material focus, and keep a fire-safe area before burning. `device frame` and `device check` never fire the beam; only the console passthrough (`console spool`) does.

1. Identify the machine: `cli-anything-meerk40t device detect [--probe]`. `device detect` globs ports only; `--probe` opens each candidate and reports firmware/version/state. Bed size, endstops, and firmware are not auto-detectable, so ask the operator for the model.
2. Establish the work origin (safety, not a command): on machines without endstops, power OFF, park the head near front-left, then power on so (0,0) is the corner. Never call `device physical-home`.
3. List profiles: `cli-anything-meerk40t machine list` (bundled and user, with origin).
4. Choose the profile: `cli-anything-meerk40t --machine PROFILE --port /dev/cu.usbserial-10 ...` sets driver, baud, and bed in one step.
5. Connect: start the persistent REPL, then `device connect` inside it. A one-shot `device connect` opens the link and shuts it down on exit, so use the REPL to keep the connection alive. Expect a controller reset click; read `$N` and `$$`.
6. Validate motion: `cli-anything-meerk40t device jog DX DY [--feed]` (MACHINE mm, origin front-left, +Y away; the beam cannot fire during `$J=` moves).
7. Move to a point: `cli-anything-meerk40t device goto X Y [--feed]`.
8. Dry-frame the area: `cli-anything-meerk40t device frame X Y W H [--feed]` (laser off).
9. Check the job: `cli-anything-meerk40t device check`. It connects and reads `$$`/`$N` automatically; it does not burn. `check()` does not disconnect, so run it in the REPL if the connection must persist. Set every op's power and speed first (the auto-created op is 100% power).
10. Capture the profile (optional): `cli-anything-meerk40t device setup --save-profile NAME`.
11. Burn: there is no dedicated burn subcommand. Run the job through the console passthrough (`cli-anything-meerk40t console 'plan default copy preprocess blob spool'`), which drives the live spooler. Start at low power and step up.

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
