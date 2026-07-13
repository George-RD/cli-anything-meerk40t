# ADR-0001: Single structured command outcome + autosave boundary

- Status: Accepted
- Issue: #26
- Date: 2026-07-14

## Context
Every CLI command and REPL line must yield exactly one observable outcome
(a success dict or an error dict), emit that outcome exactly once, and persist
via autosave exactly once after a proven mutation — with a non-zero exit code on
failure so an agent can branch on it. Before #26, output could be emitted
multiple times and failures were inconsistently signalled.

## Decision
- All commands are routed through `OutcomeCommand` (`meerk40t_cli.py:296`) and
  mutating commands are wrapped by the `@mutating` decorator
  (`meerk40t_cli.py:264`). The REPL dispatches each line through the same path.
- Output is buffered via `_emit` / `_emit_now` (`meerk40t_cli.py:113`, `:141`,
  `:149`) and flushed exactly once by the single completion boundary
  `_complete_command` (`meerk40t_cli.py:213`).
- `_complete_command` classifies the result (`error` key, `ok is False`, or a
  captured failure) and sets the exit code: `1` for a normal failure, `2` for an
  acknowledgeable gate, `0` on success (`meerk40t_cli.py:223-234`).
- On a proven successful mutation it auto-saves exactly once via
  `_autosave_once` (`meerk40t_cli.py:196`): with an active session it calls
  `sess.save(backend)`, otherwise with a bound `project_path` it calls
  `backend.save_svg(project_path)` (`meerk40t_cli.py:207-210`).
- An uncaught command exception is converted exactly once into a structured
  failure payload (`meerk40t_cli.py:278-290`); a persistence failure after a
  successful command discards the buffered success and reports only the
  infrastructure error (`meerk40t_cli.py:238-253`).
- `--dry-run` (`meerk40t_cli.py:369`) is honoured inside `_autosave_once`
  (`meerk40t_cli.py:202`) and also short-circuits the completion boundary
  (`meerk40t_cli.py:235`).
- Failure signalling is uniform across `core/`: `open_project` / `save_project` /
  `close_project` return error dicts and never raise (`project.py:63-170`);
  `operations` validation raises `ValueError` / `JobPrepError` which the boundary
  converts; `export_gcode` returns a JSON error dict for the full-power guard and
  raises `RuntimeError` only when no GRBL device is active (`export.py:127-152`);
  `device.check` returns a clean JSON error and never a traceback
  (`device.py:327-389`).

## Consequences
- Agents get deterministic `--json` outcomes and meaningful exit codes.
- Output is never emitted twice and autosave is observable and suppressible.
- A successful command that fails to persist is reported as a failure, not a
  silent success.
