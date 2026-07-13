# ADR-0004: Acknowledged motion (GRBL jog / goto / frame)

- Status: Accepted
- Issue: #29
- Date: 2026-07-14

## Context
Motion commands must never assume success. A move that the controller rejects
(or to which no hardware replies) must be reported as a failure, never as a
silent "moved".

## Decision
- `jog` / `goto` / `frame` (`device.py:584`, `:608`, `:632`) all gate on
  `_require_live_connection` (`device.py:481`), which returns an error dict
  (never a traceback) when there is no device, no writable controller, or no
  live link (`device.py:481-499`). `TestJogRefusalWithoutConnection`
  (`test_core.py:792`) locks this in.
- They emit the exact GRBL 1.1 jogging word via `_format_jog` (`device.py:567`):
  relative jog → `$J=G21G91 X.. Y.. F..`, absolute goto/frame →
  `$J=G53G21G90 X.. Y.. F..` (`device.py:573-580`). `TestJogExactStrings`
  (`test_core.py:885`) asserts the literal strings (`test_core.py:944`,
  `:954`, `:964`).
- They await the GRBL reply via `_await_jog_ack` (`device.py:502`) and parse it
  with `_parse_ack` (`device.py:548`). **An empty reply is never treated as
  success** — `_parse_ack` returns `(False, None)` for an empty reply
  (`device.py:555-556`); `TestJogExactStrings.test_jog_unacknowledged_without_reply`
  (`test_core.py:974`) enforces this.
- Each move result carries `acknowledged`, `response`, and `error`.
- `check()` (`device.py:327`) preflights `$32` (laser mode on), empty startup
  blocks, and bed bounds, returning a clean JSON dict (`device.py:369-389`); a
  failed connection yields a JSON error, never a traceback (`device.py:338`).

## Consequences
- An agent never receives a false "moved" signal.
- Connection refusal and hardware rejection are explicit, structured outcomes.
- The exact wire words are pinned by tests, so the protocol cannot drift.
