---
name: cli-anything-meerk40t
description: Use when working a laser through cli-anything-meerk40t - designing jobs headlessly (elements, operations, SVG/G-code export), detecting and preflighting a real GRBL laser, driving motion (jog, goto, frame), choosing power and speed for a material, calibrating settings on scrap, preparing material-driven jobs (materials/job/attach), diagnosing placement and motion failures, or remotely operating a running MeerK40t GUI via its console server (bridge plugin required).
---

# cli-anything-meerk40t

Agent CLI for MeerK40t laser software. Headless, `--json` output, wraps the
real MeerK40t kernel. Install: `pip install cli-anything-meerk40t` (installs
MeerK40t automatically; Python 3.10+).

The laser can injure eyes and start fires. The operator must be present,
wearing 445nm OD5+ rated glasses, for any step that moves or burns. Start
every new material at low power and step up, never down.

## Route by task

| Your task | Gate before acting | Next action | Read |
|---|---|---|---|
| Design a job offline (shapes, operations, export SVG/G-code) | none - no hardware touched | `project new`, then elements/operations/export | [references/commands.md](references/commands.md) |
| Find or verify a connected laser | serial port exists | `device detect --probe`, then `device check` | [references/commands.md](references/commands.md) |
| Move the head (jog, goto, frame) | live connection + operator present | REPL session, then `device jog/goto/frame` | [references/hardware.md](references/hardware.md) |
| Pick power/speed for a material | know the machine's `$30` scale | power ladder on scrap first | [references/materials.md](references/materials.md) |
| Prepare a material-driven job for the GUI | material profile exists; GUI kernel live | `job prepare`, then `job preflight`, then `attach stage` | [references/materials.md](references/materials.md) |
| Burn a job | full safety gate + connection verified Idle (below) | connect, verify, re-frame at the burn location, then spool | [references/hardware.md](references/hardware.md) |
| Head offset, noise, or lost position | stop motion first | diagnose before trusting placement | [references/hardware.md](references/hardware.md) |
| Operate a running MeerK40t GUI remotely (console server) | bridge plugin loaded (`bridge_status`) | connection-first gate, stage job, operator presses Start | [references/gui-operation.md](references/gui-operation.md) |

## Non-negotiable gates

- **One-shot commands drop the connection on exit.** Any sequence needing a
  live link (connect, jog, goto, frame, burn) runs inside the REPL
  (`cli-anything-meerk40t repl`), not as separate invocations.
- **No endstops means no homing.** On machines without endstops the operator
  parks the head front-left with the machine POWERED OFF, then powers on:
  that position is (0,0). Never call `device physical-home` there.
- **Auto-created operations default to 100% power.** Set power and speed on
  every operation before export or burn.
- **Connection before job.** A spooled job "runs" even when the serial link
  never opened - steps count up, nothing moves. Order is fixed: port
  enumerated -> `device connect` -> status reports Idle, no alarm -> stage the
  job -> burn. Never queue work against an unverified connection.
- **Frame before burn.** `device frame X Y W H` dry-traces an explicit
  rectangle beam-off - pass the job bounds yourself, it does not derive them.
  Re-frame after any snag, reposition, or new material.
- **Coordinates:** `jog`/`goto`/`frame` take MACHINE mm - origin front-left,
  +Y away from the operator. Design space (SVG) is top-left; the export
  applies the Y-flip and prints a placement summary to verify.

## Material profiles and burn prep

Settings are resolved from a material profile, not hand-typed per job. A
material file carries the physics (cut/score/etch power, speed, passes) per
machine; a design only tags strokes by colour. Swapping card means swapping the
`--material` argument, never the job code.

`--json` and `--machine` are root options: pass them before the subcommand,
for example `cli-anything-meerk40t --json --machine sculpfun-s9 materials list`.

### Materials

- `materials list` - show every material with its origin (bundled or user).
- `materials show NAME [--machine M]` - full profile, or just machine M's
  role settings.
- `materials create NAME --description TEXT [--machine M]` - write an empty
  user material. Every role is missing until you record it, so a fresh
  material cannot prepare any job: calibration is structurally required, not
  advisory.
- `materials record NAME --machine M --role R --power P --speed S --passes N
  --provenance {tested,estimated} --note TEXT` - add or overwrite a role.
  Evidence rule: `--provenance tested` requires a `--note` of at least 20
  characters describing the test burn (material, pattern, settings, what was
  seen, date). A shorter or missing note is rejected and nothing is written.
  `estimated` accepts any note: a rationale, not an observation.

### The provenance gate

Each role is `tested` (observed) or `estimated` (reasoned). `job prepare`
refuses any estimated role unless you pass `--allow-estimated`. The refusal is
an acknowledgeable gate: it exits 2 (not a hard error) and names the untested
roles, so the operator can accept the risk on scrap before continuing. A
calibration ladder (below) is how an estimated role becomes tested.

### Calibration ladder

`job ladder --out-dir DIR --role R --powers P1,P2,... --speed S [--passes N]
[--length MM] [--pitch MM]` writes a scrap-sized test pattern: one short line
per power step, all at the same feed. Burn it on scrap of the target material,
find the winning step, then `materials record ... --provenance tested --note
'...'`. A ladder needs `--machine` (bed bounds) but not `--material`; it is the
sanctioned calibration route, so `attach stage` accepts a ladder manifest
without `--allow-estimated`.

### Canonical burn-prep path

```
cli-anything-meerk40t --json --machine sculpfun-s9 job prepare design.svg \
  --out-dir /tmp/j --material kraft-350gsm
# exits 2 if any role is estimated; add --allow-estimated to acknowledge
cli-anything-meerk40t job preflight /tmp/j/design_manifest.json --allow-estimated
# re-verifies file hashes and settings fingerprint; prints the operator checklist
cli-anything-meerk40t attach --port 2323 stage /tmp/j/design_job.svg \
  /tmp/j/design_manifest.json --allow-estimated
# verifies the manifest, then loads the job SVG on the live GUI kernel
```

`job preflight` prints the operator checklist (informational; the human gate
stays human). Confirm each item before the operator starts the burn:

- sheet placed and bed origin confirmed
- overhang supported flat on the bed
- diode focused on the material surface
- rated laser-safety glasses on
- ventilation running
- extinguisher or fire blanket within reach
- operator stays for the entire burn

For a ladder manifest the checklist adds one line: burn on scrap of the target
material only.

### GRBL bring-up still applies

The material workflow prepares and stages the job; it does not replace the
hardware safety gates. Before any burn the operator must still set the work
origin, confirm material focus, wear rated glasses, and keep a fire-safe area.
See [references/hardware.md](references/hardware.md) and the fallback note
below.

### Fallback: raw console or telnet staging

The agent-facing path is `job prepare` + `job preflight` + `attach stage`; the
last step always verifies the manifest before loading. If you must stage by
hand, the raw `console` passthrough and the GUI telnet console still work
(load the job SVG, let the operator press Start), but they skip the manifest
check. Prefer the verified path; use the raw console only as a fallback.

## References

- [references/commands.md](references/commands.md) - full CLI reference:
  syntax, command groups, device commands, machine profiles, community
  profile submission, worked examples.
- [references/hardware.md](references/hardware.md) - safety gates, origin
  discipline, GRBL preflight meaning (`$32`, `$N`, `$130/$131`, `$30`),
  coordinate conventions, failure diagnosis, when to re-frame.
- [references/materials.md](references/materials.md) - power and speed
  method, the power-ladder first test, field-verified data points.
- [references/gui-operation.md](references/gui-operation.md) - remote GUI
  operation over the console server: bridge-plugin gate, connection-first
  order, staging jobs the operator starts, telnet quirks. **The consoleserver
  is unauthenticated — keep it on loopback and firewall-restrict it (see that
  reference).**

The CLI is the default, fully supported path. Remote GUI operation needs the
bundled bridge plugin (it back-fills upstream meerk40t#3249 fixes on stock
MeerK40t at runtime); the GUI's Outline/trace has unverified beam behaviour
and is never a substitute for `device frame`.
