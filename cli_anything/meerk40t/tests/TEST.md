# cli-anything-meerk40t — Test Strategy

Durable testing contract for the harness. Per-test inventory is intentionally
omitted; the suites below are the load-bearing guarantees and the assertions
that pin them.

## Philosophy
- **Real kernel, no mocks.** Every test boots the real MeerK40t kernel
  headlessly via `Meerk40tBackend` (`../utils/meerk40t_backend.py`). The backend
  under test is the genuine article; hardware (serial/USB) is never opened.
- **unittest-style, run by pytest.** Cases live in `cli_anything/meerk40t/tests/`
  and are collected by `pytest`. A `conftest.py` caches the real top-level
  `meerk40t` package before any test module imports, fixing an order-dependent
  import-shadow bug.
- **Behaviour over plumbing.** Tests assert observable contracts (exit codes,
  JSON shapes, exact GRBL words, hash verification) — not source text.

## How to run
```bash
# Focused gate (behavioural units):
.venv/bin/python -m unittest cli_anything.meerk40t.tests.test_core -v
# Full sweep:
.venv/bin/python -m pytest cli_anything/meerk40t/tests/ -q
```
Both test modules create a fresh backend in `setUp` and tear it down in
`tearDown`. E2E tests that exercise the installed CLI fall back to
`python -m cli_anything.meerk40t.meerk40t_cli` when the console script is
absent.

## Load-bearing invariant suites
- **`TestJogRefusalWithoutConnection`** (`test_core.py:792`) — `jog`/`goto`/`frame`
  refuse without a live connection and return a structured error.
- **`TestJogExactStrings`** (`test_core.py:885`) — motion emits the exact GRBL 1.1
  jog words (`$J=G21G91 …`, `$J=G53G21G90 …`) and reports acknowledgement instead
  of assuming success (empty reply ⇒ unacknowledged).
- **`TestSkillPackaging`** (`test_core.py:1434`) — the packaged `skills/SKILL.md`
  and every linked `references/*.md` are byte-identical to the canonical
  `skills/cli-anything-meerk40t/` tree; self-skips on an installed wheel.
- **`TestJobManifest` / `TestStageFileScene`** (`test_core.py`) — the job manifest
  records per-file sha256 + settings fingerprint + verification verdict, the CLI
  preflight rejects tampered G-code, and receiver staging replaces the scene
  (never accumulates) and refuses a hash mismatch.
- **`TestExportGuard`** (`test_core.py`) — G-code export refuses operations still
  at default power (1000) unless `allow_full_power` is set.
- **`TestSessionPersistence`** (`test_core.py`) — session JSON saves/loads and
  undo/redo move commands through the history stacks.

## Coverage snapshot
162 tests pass (131 unit across `test_core.py` + `test_mk_plugin.py`, 31 E2E in
`test_full_e2e.py`) — backend wrapper, project/session transactions, elements,
operations, device/GRBL, export guards, job-prep provenance + manifest, skill
packaging, materials, and client-frame attach over a live consoleserver.
Re-run the gate after any change to `core/`, `utils/`, or `mk_control.py`.
