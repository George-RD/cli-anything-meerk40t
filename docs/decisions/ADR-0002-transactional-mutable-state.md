# ADR-0002: Transactional mutable state (project / session)

- Status: Accepted
- Issue: #27
- Date: 2026-07-14

## Context
Project open/save and session undo/redo mutate the live kernel tree and disk.
A crash mid-operation must never leave a corrupt half-state, a stale prior
scene, or a lingering temp file.

## Decision
- `open_project` (`project.py:63`) loads the candidate by **appending** to the
  existing tree, then: on load failure it strips only the appended nodes
  (rollback, `project.py:84-89`); on an empty/invalid inventory it keeps the
  prior scene (`project.py:90-100`); on success it removes the prior scene
  leaving only the candidate (`project.py:101-108`). The target file is never
  written by open.
- `save_project` (`project.py:111`) renders to a same-directory temp file,
  verifies `save_svg` succeeded and the file is non-empty, then `os.replace`s it
  onto the target so any earlier failure leaves the prior target byte-identical
  (`project.py:118-128`). The temp is removed in `finally` **only** if it was
  not atomically replaced (`project.py:132-140`).
- `close_project` (`project.py:161`) clears the tree and **fails closed** if
  `elem_count() > 0` afterwards (`project.py:161-170`).
- `Session.save` (`session.py:36`) writes the session JSON via `atomic_write_json`
  (`session.py:8`, `:66`) under an exclusive lock. Undo/redo are **command-string
  stacks** (`session.py:74-85`), not state snapshots, so the session file is a
  small durable log of `{cmd, ts}` plus the undo/redo stacks (`session.py:69-85`).
- Non-fatal cleanup in `project.py` uses `except Exception: pass` only for
  best-effort clears where the caller already holds an error dict
  (`project.py:21,35,40,52,59,140`); this is deliberate, not a swallowed fault.

## Consequences
- Project writes are atomic; open never corrupts the prior scene.
- Session persistence is a locked, append-style JSON log.
- Close refuses to report success if the tree did not clear.
