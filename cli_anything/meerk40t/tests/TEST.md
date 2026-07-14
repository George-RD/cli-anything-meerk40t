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
# Dependent-module gate (must be green before PR):
CLI_ANYTHING_FORCE_INSTALLED=1 .venv/bin/python -m unittest \
  cli_anything.meerk40t.tests.test_core \
  cli_anything.meerk40t.tests.test_mk_plugin \
  cli_anything.meerk40t.tests.test_full_e2e
# Full sweep:
.venv/bin/python -m pytest cli_anything/meerk40t/tests/ -q
```
Both test modules create a fresh backend in `setUp` and tear it down in
`tearDown`. E2E tests that exercise the installed CLI fall back to
`python -m cli_anything.meerk40t.meerk40t_cli` when the console script is
absent.

## Clean-install invariant (regression guard for #51)
The suite MUST pass on a **pristine** `meerk40t==0.9.9100` install as well as on
the editable dev install. Run it from a neutral cwd with `PYTHONPATH` unset so a
stray dev build cannot shadow the clean package:
```bash
cd /tmp && unset PYTHONPATH
CLI_ANYTHING_FORCE_INSTALLED=1 /tmp/ca_clean/venv/bin/python -m unittest \
  cli_anything.meerk40t.tests.test_core \
  cli_anything.meerk40t.tests.test_mk_plugin \
  cli_anything.meerk40t.tests.test_full_e2e
```
**Why both installs matter.** Each test boots a fresh kernel, so patch/loader
state is process-local and does **not** leak across runs. The real divergence is
the **install/code version**: the dev (editable) build and the clean
`meerk40t==0.9.9100` package differ in two ways that alone can mask or break the
back-fill and staging logic — (1) both installs run `apply_backfill_patches` at
plugin boot, but dev's upstream `set` command already starts fixed (so the
back-fill no-ops) whereas clean 0.9.9100 ships a broken `set` (so the back-fill
(2) the clean package's `elements.load` is additive for user elements but **removes the
 boot-default ops** during load (the staged elements are appended); the net replace of
 the prior scene is performed by `_commit_replacement` detaching pre-existing roots on
 commit. The dev build is fully additive. Tests MUST be hermetic and never
assert against an install-specific starting state. The `mk_plugin` tests force the
code path under test directly (`UPSTREAM_FIXED_VERSION` for the version
short-circuit; a `_get_registered_set` lookup patch to a marker-verified fixed
command for the behavioural detectors) so they are green on both installs. The
The `mk_control` staging tests seed deterministic pre-existing roots via the
project's `operations.add_operation` API and snapshot/restore scene roots
explicitly (`_snapshot`) rather than relying on a freshly-empty scene or
backend boot defaults.

**Loader invariant.** On the clean `0.9.9100` package, `elements.load` is additive for
 elements but **removes the boot-default ops** during load (staged elements are appended);
 on the dev build it is fully additive. The net "replace" of the prior scene is driven by
 `_commit_replacement` detaching pre-existing roots on a successful commit. Production
 rollback (`mk_control._restore_pre`) restores the boot-default ops the loader displaced,
 re-attaching only roots that are no longer live, on every refusal / commit-failure path.
 A green result on the clean install is the proof that the displaced boot-default ops are
 restored rather than silently dropped.

## Load-bearing invariant suites
- **`TestJogRefusalWithoutConnection`** (`test_core.py:792`) — `jog`/`goto`/`frame`
  refuse without a live connection and return a structured error.
- **`TestJogExactStrings`** (`test_core.py:885`) — motion emits the exact GRBL 1.1
  jog words (`$J=G21G91 …`, `$J=G53G21G90 …`) and reports acknowledgement instead
  of assuming success (empty reply ⇒ unacknowledged).
- **`TestSkillPackaging`** (`test_core.py:1434`) — the packaged `SKILL.md`
  and every linked `references/*.md` are byte-identical to the canonical
  `skills/cli-anything-meerk40t/` tree; self-skips on an installed wheel.
- **`TestJobManifest` / `TestStageFileScene`** (`test_core.py`) — the job manifest
  records per-file sha256 + settings fingerprint + verification verdict, the CLI
  preflight rejects tampered G-code, and receiver staging is transactional:
  the receiver swaps to the staged scene and restores the prior scene **verbatim**
  on any refusal, a hash mismatch, a machine-binding mismatch (live device !=
  manifest machine), an inventory mismatch (staged op/element set != manifest),
  staged geometry outside the live bed, and rolls back exactly to the prior
  scene on a commit failure ("rollback incomplete" only if a node cannot be
  re-attached). The machine-binding helper is unit-tested directly
  (`test_check_machine_binding_helper`); the mandatory binding gate is also
  exercised end-to-end against a live consoleserver kernel (see below).
- **`TestIssue31Phase1.test_concurrent_stage_clients_correlated`** (`test_full_e2e.py`) — two `stage` requests sent concurrently over separate sockets are serialized by a **per-kernel staging lock** (lazily created under a module-level bootstrap guard; the same helper backs both `register` and `_stage_file`). Without it, concurrent `elements.load` calls inflate the shared `added_ids` set and `_check_inventory` sees 6 staged elements vs the manifest's 3. With the lock, both replies are correlated by `request_id` and report `elements == 3`.
- **`TestExportGuard`** (`test_core.py`) — G-code export refuses operations still
  at default power (1000) unless `allow_full_power` is set.
- **`TestSessionPersistence`** (`test_core.py`) — session JSON saves/loads and
  undo/redo move commands through the history stacks.
## Coverage snapshot

The full suite passes on a **clean** `meerk40t==0.9.9100` install (`CLI_ANYTHING_FORCE_INSTALLED=1`, run from a neutral cwd with `PYTHONPATH` unset — see the Clean-install invariant above). The dev (editable) install passes the same gate; one test, `TestIssue31Phase1.test_concurrent_stage_clients_correlated`, is concurrency/network-sensitive and may intermittently fail on the dev install only — it is unrelated to #51 and green on the clean install:
```bash
CLI_ANYTHING_FORCE_INSTALLED=1 \
  .venv/bin/python -m unittest \
  cli_anything.meerk40t.tests.test_core \
  cli_anything.meerk40t.tests.test_mk_plugin \
  cli_anything.meerk40t.tests.test_full_e2e
```
The full sweep covers backend wrapper, project/session transactions, elements,
operations, device/GRBL, export guards, job-prep provenance + manifest, skill
packaging, materials, and client-frame attach over a live consoleserver. The
`test_full_e2e` kernel boots a **headless grbl device** (`service device
start -i grbl 0`, no serial port opened) so the mandatory machine-binding gate
has a live device to bind against (provider module + 410×400 mm bed matching
the sculpfun-s9 profile) and `elements.load` runs against a valid device context.
Re-run the gate after any change to `core/`, `utils/`, or `mk_control.py`.
