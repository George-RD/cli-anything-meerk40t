---
name: cli-anything-meerk40t
description: Use when working a laser through cli-anything-meerk40t - designing jobs headlessly (elements, operations, SVG/G-code export), detecting and preflighting a real GRBL laser, driving motion (jog, goto, frame), choosing power and speed for a material, diagnosing placement and motion failures, or remotely operating a running MeerK40t GUI via its console server (bridge plugin required).
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
  order, staging jobs the operator starts, telnet quirks.

The CLI is the default, fully supported path. Remote GUI operation needs the
bundled bridge plugin (it back-fills upstream meerk40t#3249 fixes on stock
MeerK40t at runtime); the GUI's Outline/trace has unverified beam behaviour
and is never a substitute for `device frame`.
