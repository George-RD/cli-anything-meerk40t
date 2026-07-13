# cli-anything-meerk40t foundational remediation plan

## Context

The audit and the independent blindspot pass agree: the repository has a strong real-MeerK40t integration and a passing 162-test local suite, but it treats attempted work as committed work before postconditions are proven. The failures cluster around five boundaries: command completion, project/session persistence, artifact staging, physical transport, and release publication.

The target is deliberately small: one command outcome rule, one autosave path, transactional project and staging transitions, receiver-verified burn inputs, acknowledged motion, and build-once/test-the-wheel-before-publish releases. No new service layer or alternate project format.

Research methods used:
- **blindspot-pass:** independently rechecked every audit finding against the current tree; none were invalidated. It identified staging integrity and pre-kernel validation as shared invariants rather than unrelated bugs.
- **reference-hunt:** verified the relevant MeerK40t 0.9.x APIs and semantics in the installed editable source. In particular, `elements.load(...)` does not clear the existing tree; console loader failures are channel output; `GRBLController.write(...)` is asynchronous; controller lifecycles and state fields differ by provider.
- **implementation-plan:** puts the most reversible behavior decisions first, then orders implementation by dependency, with observable gates after every wave.

## Decisions to ratify before implementation

These are the only choices likely to change product behavior. Defaults are selected for the safest boring implementation.

1. **Failure exit codes.** Default: exit `1` for ordinary command/postcondition failures; retain exit `2` only for operator-acknowledgeable safety gates such as estimated roles or failed preflight. This preserves the current CLI convention.
2. **Project open semantics.** Default: a missing or malformed path is an error and never means “create a blank project.” `project new` remains the only creation command.
3. **Stage compatibility.** Default: make the attach wire change a clean protocol cutover. Client and bridge must both speak the request-ID + manifest-bound frame contract; no legacy uncorrelated fallback.
4. **Motion timeout semantics.** Default: timeout means `indeterminate`, never success and never automatic retry. The operator must query status before another movement.
5. **Publish permissions.** Default: tests/build have no OIDC; only the final artifact-consuming publish job receives `id-token: write`, ideally under a protected `pypi` environment.

## Foundational architecture

### A. One command outcome rule

Keep dictionaries as the public JSON shape; do not introduce a broad result-class hierarchy. Establish these invariants in `meerk40t_cli.py`:

- Success: a mapping with no `error`, emitted once, exit `0`.
- Failure: a mapping with `error` plus relevant context, emitted once, nonzero exit.
- A mutating callback returns its core result to one `_complete_command(ctx, result, mutates=True)` boundary.
- `_complete_command` checks the core result first, performs the single autosave path only after successful mutation, verifies save success, and only then emits success.
- Infrastructure exceptions are converted there into the same structured failure contract. There is no `except: pass` on a user-visible path.
- Core mutation functions prove their postcondition: compare element/operation count or read back the changed value. “Console command was sent” is not success.

This replaces the current `@mutating`/`_auto_save` behavior and the separate REPL autosave path. It does not alter existing successful payload fields unnecessarily.

### B. Transaction boundary for mutable state

Use stage → validate → commit for project/session/material writes:

1. Snapshot the active backend tree and active path metadata.
2. Load/validate a candidate without destroying the active state. For project open, validate in a temporary `Meerk40tBackend`; for the final active load, use the typed MeerK40t loader API rather than interpolating a path into a console string.
3. Commit the new tree and path binding only after the loader and inventory postconditions succeed.
4. On any failure, restore the old tree and binding and do not autosave.
5. Write session and user-material JSON through a same-directory temporary file, `fsync`, then `os.replace`; use `fcntl.flock` on POSIX and a tested lock-file fallback where unavailable.

`project open` and `project close` are lifecycle transitions, not ordinary mutations. They must never autosave to the pre-transition path. With `--session`, restore the recorded backing SVG before dispatch when no explicit `--project` is supplied; explicit `--project` wins.

### C. Artifact trust boundary

Treat the manifest and job SVG as one immutable envelope:

- Define and validate the full `clia-job-manifest-v1` shape in one function shared by `job preflight`, `attach stage`, and the issue/profile automation where applicable. Validate nested objects before dereference and reject unknown/malformed required fields with structured errors.
- Reject `NaN`/infinity and all non-positive dimensions, powers, feeds, passes, pitches, and derived coordinates. JSON loads use strict non-finite rejection.
- Every visible drawable must be assigned or explicitly refused; fill-only artwork cannot silently disappear.
- Generate each output as a canonical in-memory byte buffer, write exactly those bytes, and compute its digest from that same buffer. `_write_manifest` stores only `os.path.relpath(output_path, manifest_path.parent)`; it never stores `Path.resolve()` output and never reopens an output to hash it.
- On consumption, resolve each relative artifact path with `Path.resolve(strict=True)` and require `os.path.commonpath([manifest_root, resolved]) == str(manifest_root)` unless the schema explicitly declares an external-artifact kind; reject `..` traversal and symlink escape before reading.
- On consumption, read the manifest and SVG paths exactly once into immutable in-memory byte buffers, resolving artifact paths only against the manifest directory. Parse, validate, hash, transmit, and compare those same buffers; never re-read either path after hashing. Bind the staged request to those exact bytes, manifest SHA-256, SVG SHA-256, machine/profile fingerprint, provider, bed dimensions, coordinate convention, operation inventory, and power/feed/pass limits.
- Receiver revalidates schema and hashes, derives live comparable device/bed/provider values, and rejects mismatch before touching the scene.
- Receiver loads through a controlled temporary path/typed loader, validates element and operation inventory, then swaps the scene. On load or validation failure, retain the previous staged job.

### D. Explicit transport acknowledgement

There are two protocols and both need correlation:

- **Attach frames:** add a cryptographically random `request_id` to every request and reply. The client ignores unmatched frames. Status cannot satisfy stage. Keep the wire frame single-line JSON, but version it and fail closed on missing IDs.
- **Controller commands:** serialize writes under a per-controller lock, drain stale replies before the write, and accept only the exact GRBL terminal response for that command (`ok` success; `error:`/`ALARM:` failure). Status lines such as `<Idle|...>` are observations, not acknowledgements.

Normalize controller behavior through a small provider adapter for lifecycle (`connect`/`disconnect`), connectivity, state, and command acknowledgement. Do not force Newly/Balor through GRBL `open`/`close` assumptions. Normalize the full GRBL state vocabulary, including `Hold:*`, `Door:*`, and `Check`; preserve the base state and optional substate.

Relative motion must remain relative end to end (`move_relative` or controller-native equivalent). Jog/frame stop on the first rejected corner, return `framed: false` with the partial trace, and never queue the remaining moves. Legacy `home`, `physical-home`, and `move` route through the same connected/acknowledged gate as jog/frame.

### E. Preflight derives safety; it never trusts a stored verdict

Replace explicit-`S` line inspection with a modal G-code state machine that tracks:

- units (`G20/G21`), coordinate mode (`G90/G91`), motion (`G0/G1/G2/G3`), laser state (`M3/M4/M5`), effective `S`, effective `F`, and current XY;
- power/feed on every powered movement, including inherited modal values and arcs;
- machine bounds over the actual motion envelope;
- requested pass and ladder-power coverage, operation ordering, and shutdown state.

Every `job preflight` and `attach stage` recomputes these facts from artifact bytes and trusted machine/material settings. The manifest’s prior `verification.all_passed` is historical evidence only, never authority. `job ladder` exits nonzero and omits usable burn/record instructions whenever verification fails.

## Execution bootstrap and TDD contract

This is **Phase 0** and precedes behavioral implementation. It creates durable context and a single progress ledger so the work can continue safely across sessions.

### Canonical information architecture

Each fact has one home; other surfaces link rather than copy it:

- `DESIGN.md`: stable product goals, non-goals, current architecture, target architecture, and load-bearing invariants.
- `docs/decisions/`: append-only architecture decision records for ratified choices and their change gates. Initial records cover the command outcome contract, transactional state transitions, receiver-verified immutable burn artifacts, acknowledged physical motion, and build-once/test-the-wheel publication.
- GitHub umbrella issue: the only volatile execution ledger. It owns dependency order, wave status, blockers, and links to implementation issues and merged PRs.
- One GitHub implementation issue per independently reviewable slice: exact scope, affected symbols, acceptance criteria, dependencies, TDD cases, focused gate, and definition of done. A fresh session must be able to execute one issue without relying on chat history.
- `SHARED_CONTEXT.md`: replace the stale scaffold-era content with a short boot index pointing to `DESIGN.md`, the decision records, the umbrella issue, `BACKEND_CONTRACT.md`, and the test documentation. It must not duplicate progress or architecture prose.
- `cli_anything/meerk40t/tests/TEST.md`: test inventory and durable test strategy only. Live task status belongs in GitHub, not in this file.

### Phase 0 deliverables

1. Reconcile `DESIGN.md` and `BACKEND_CONTRACT.md` with the audited current tree and target invariants; remove stale path and capability claims.
2. Add the initial decision records. Each record states context, decision, consequences, rejected alternatives, and what evidence is required to revisit it.
3. Replace `SHARED_CONTEXT.md` with the boot index described above.
4. File self-contained implementation issues in this dependency order:
   - command outcome contract and one autosave completion boundary;
   - transactional project/session state;
   - strict operation property validation and persistence;
   - atomic, fail-closed material profile persistence;
   - strict manifest schema and modal G-code verification;
   - versioned, correlated, transactional staging;
   - provider-aware acknowledged physical transport;
   - isolated profile contribution and profile-to-PR automation;
   - build-once/test-the-wheel publication;
   - copied-example verification and final documentation reconciliation.
5. Create the umbrella issue last, linking every implementation issue and recording the dependency DAG. The tracker checklist is updated only when the corresponding PR is merged and its required gate is green.
6. Validate every issue body against current file paths and symbols before filing. Use existing repository labels rather than inventing unverified labels.

### Dependency graph

Every implementation issue declares one machine-readable `Depends on:` line that is the executable graph. The umbrella issue #36 mirrors these as per-issue tracking annotations; those annotations are documentation of the same edges, not additional prerequisites, and #36 itself is never a prerequisite for any issue. The authoritative graph is:

- #25: none.
- #26: #25.
- #27: #26.
- #28: #26.
- #29: #25.
- #30: #28 and #29.
- #31: #27 and #30.
- #32: #25.
- #33: #25.
- #34: #25 (final merge follows the runtime waves so its clean-wheel smoke installs the remediated package).
- #37: #25.
- #35: #26, #27, #28, #29, #30, #31, #32, #33, #34, and #37; deliberately last.
- #36: umbrella tracker only; it mirrors every edge above as a checklist annotation and has no execution dependency of its own.

Parallel-safe after #25 merges: #26, #29, #32, #33, #34, and #37 may execute concurrently. #27 and #28 may execute concurrently after #26. #30 follows #28 and #29. #31 follows #27 and #30. #35 follows all prior implementation issues.

Issue #31 owns the receiver-verified immutable attach-staging implementation. Closed issue #23 is superseded history only and is never a coordination dependency.

### Mandatory TDD loop for every behavioral issue

The required regressions in each wave are not a post-implementation test list; they are the implementation driver.

This loop is mandatory only for behavioral issues. Documentation, contribution-automation, and release-automation issues introduce no behavioral unit-testable change; they instead require issue-specific before/after evidence for each acceptance criterion and their stated focused gates (link/path validation, copied-example preflight, installed-wheel offline quick start, injected-failure CI runs, and the SKILL synchronization check). Phase 0 issue #25 is documentation-only and is exempt from the behavioral red/green requirement.

1. **Red:** add the smallest observable regression test for one invariant and run its focused selector. Record the command and expected failure in the issue or PR. A test that passes before the production change does not establish the regression and must be corrected.
2. **Green:** make the smallest production change that satisfies that test. No unrelated cleanup or abstraction.
3. **Refactor:** remove duplication and obsolete paths only after green; rerun the focused selector.
4. Repeat red/green/refactor for the next acceptance criterion.
5. Run the issue's dependent-module tests before opening the PR.
6. Run the complete real-backend suite at the wave boundary. For physical motion, use deterministic controller/provider test doubles for error, timeout, stale-response, and state-machine cases; run the explicitly gated real-device smoke only with the documented safe setup.
7. The PR must link and close exactly one implementation issue, include the red/green evidence for a behavioral issue or the issue-specific before/after evidence for a documentation, contribution, or release issue, list the focused and wave gates run, and leave the umbrella checklist untouched until merge.

Tests assert user-visible contracts and postconditions: exit status, structured error shape, preserved bytes, preserved scene, resulting inventory, correlated acknowledgement, immutable wheel identity, and resource availability. They must not assert source text or incidental internal calls. Every failure-path test must also assert the old durable/live state remains intact.

### Cross-session operating loop

A fresh session starts from the umbrella issue, selects the earliest unblocked issue, reads only that issue plus linked architecture/decision records, and verifies the stated baseline. It marks the issue in progress, then executes it through the behavioral red/green/refactor loop or, for a documentation, contribution, or release issue, the before/after evidence path defined above, opens a PR, and updates the tracker after merge. New discoveries are recorded on the active issue; architecture changes require a decision-record amendment or superseding record. Chat transcripts and ad-hoc handoff files are never the system of record.

### Cross-session claim protocol

Before editing an issue, an executor records in that issue: the issue number, an executor/session identifier, the branch, the start time (UTC), and an expiry (UTC).

- Claim only an issue whose dependencies are all merged and whose current claim is absent or expired.
- Refresh the claim while work is active.
- After a claim expires, another executor may take it over from repository evidence alone.
- Clear the claim after the PR merges or on explicit abandonment.
- Update dependency, status, or checklist state only after the referenced PR is merged and its required gates are green.
- Never infer readiness from wave proximity, sibling status, or chat history; derive it only from merged dependencies and repository evidence.

## Implementation waves

### Wave 1 — command and persistence transactions

**Changes**
1. Add the centralized completion/autosave boundary in `meerk40t_cli.py`; migrate every mutating CLI and REPL path to it.
2. Make `core/operations.py` validate supported operation types and property schemas before dispatch, verify count/readback afterward, and return structured failures. Remove swallowed exceptions.
3. Make backend `save_svg` and typed load APIs verify postconditions and expose loader diagnostics.
4. Rework `core/project.py` open/close/save around transaction semantics and delayed path rebinding.
5. Rework `core/session.py` to restore its SVG, save atomically, and surface persistence errors.
6. Make user-material writes atomic and malformed same-name overrides fail closed instead of falling through to bundled defaults.

**Gate**
- Existing project bytes survive failed open, failed close, invalid extension autosave, and save failure.
- `--project A project open B` changes only B and never overwrites A.
- `--session` alone restores the recorded SVG; explicit `--project` precedence is deterministic.
- Invalid operation type/value cannot mutate SVG or MeerK40t’s persistent operation defaults; a fresh backend still starts.

### Wave 2 — finite inputs, manifest schema, and modal G-code

**Changes**
1. Introduce one strict manifest validator and strict JSON loader.
2. Validate all numeric material/machine/job inputs with `math.isfinite` and positive-range/domain rules.
3. Make role assignment exhaustive for every visible drawable, including fill-only nodes.
4. Replace `_parse_gcode`/`_verify_gcode_file` line-local checks with the modal verifier described above.
5. Make `_write_manifest` write and hash the same canonical output bytes, store manifest-relative paths only, and remove all post-write artifact rereads.
6. Make `_run_preflight` resolve artifacts against the manifest directory, recompute from current bytes and trusted settings, and never trust stored `all_passed`.
7. Make ladder generation fail closed.

**Gate**
- Tests reject NaN/infinity, zero/negative values, missing nested manifest objects, wrong nested types, fill-only unmapped artwork, modal inherited unsafe `S/F`, unsafe arcs, omitted ladder power, out-of-bounds relative motion, and a stale/tampered positive manifest verdict.
- A copied manifest/artifact bundle preflights after changing the process CWD. A test replaces or mutates the source path after output bytes are captured and proves the written bytes, digest, and staged bytes remain identical.

### Wave 3 — transactional, machine-bound staging

**Changes**
1. Version the attach frame and add `request_id` correlation.
2. Read manifest and job files once into immutable byte buffers, derive hashes from those buffers, and send the same bytes and hashes as the staged envelope—not paths interpreted or re-read by either side.
3. Receiver validates schema, machine/provider/bed/profile fingerprint, limits, hashes, and current preflight before scene mutation.
4. Replace console path interpolation with a controlled typed load and rollback-capable scene transaction.
5. Return success only after the new scene’s element/operation inventory matches the manifest.

**Gate**
- Stage rejects a different machine/profile, malformed manifest, wrong hash, empty/partial operations, spaced/metacharacter paths, and preflight drift without changing the live scene.
- Two interleaved clients cannot consume one another’s replies.
- A real-backend test proves the previous scene survives loader failure and that a valid staged job fully replaces rather than accumulates.

### Wave 4 — physical transport correctness

**Changes**
1. Add provider lifecycle/state adapters and normalize full GRBL states.
2. Route all motion commands through connection + exact-ack gates.
3. Preserve relative/absolute semantics through dispatch.
4. Correlate serialized controller writes with their terminal replies; timeout is indeterminate and not retried.
5. Fail frame immediately on the first rejected corner and return partial trace.

**Gate**
- Tests cover stale reply before command, interleaved status, `error:`/`ALARM:`, timeout, busy/hold/door/check states, relative movement from a nonzero origin, first-corner failure, and each supported provider lifecycle shape.
- No movement command returns success without a correlated controller acknowledgement or confirmed spooler result.

### Wave 5 — isolated contribution and release boundaries

**Changes**
1. Reimplement `profile submit --yes` so it creates a `TemporaryDirectory`, clones the canonical `REPO` into it, verifies the clone's `origin` resolves to the expected `George-RD/cli-anything-meerk40t` owner/name, and only then creates a fresh submission branch from the canonical base commit, writes, commits, pushes, and opens the PR from that disposable checkout. Verify before mutation that the generated branch is absent both locally and in the intended fork/remote; any collision fails closed without changing an existing branch. `_repo_root()` and the caller's CWD are never part of the mutating path. Every subprocess failure is returned as a structured command failure; `gh repo fork` may be tolerated only after explicitly proving its error means "fork already exists," never via a blanket `CalledProcessError` catch and never as a silent issue-URL fallback that implies submission succeeded.
2. Reuse package profile validation in `profile-to-pr.yml`; provide `GH_TOKEN`, check every command result, and stop on failed issue comments/validation. Treat the issue-comment check as advisory only: it cannot serialize all issue-edit events, so the workflow must re-fetch the live issue immediately before branch creation and again immediately before push (or use an equivalent optimistic-concurrency token), then abort if the profile block, label, state, or other validated input changed.
3. Split `publish.yml` into:
   - test/build job: no OIDC, run the documented suite, build once, `twine check`, compute a SHA-256 checksum manifest over an exact allowlist of the wheel and sdist, and upload only the distributions plus checksum manifest as one immutable artifact bundle. Expose `actions/upload-artifact`’s numeric `artifact-id` as a job output;
   - clean-wheel job: download by that exact numeric `artifact-id` via `artifact-ids`, reject missing, renamed, or additional files before checking every allowlisted distribution against the uploaded SHA-256 manifest, install the verified wheel in a clean environment, then verify packaged resources, `--help`, `--json`, and a representative real-backend command;
   - publish job: depend on both prior jobs, download by the same numeric `artifact-id`, independently enforce the exact allowlist and verify its SHA-256 manifest again immediately before upload, publish only the verified distributions, use a protected environment, and be the only job with `id-token: write`. Preserve/publish the checksum manifest with the release evidence rather than trusting filenames or artifact names.
4. Pin every third-party GitHub Action by full commit SHA and pin/lock release tooling inputs.

**Gate**
- Invoke submission from an unrelated temporary Git repository and prove its HEAD, branch, remotes, index, and files are byte-for-byte unchanged after both successful submission and injected failures at fork, clone, remote validation, checkout, write, commit, push, and PR creation.
- A non-“already forked” fork failure and every later command failure return `submitted: false`, the failing command/stage, and a nonzero CLI outcome; remote owner/name mismatch fails before checkout or file creation in the disposable clone.
- CI proves source tests, package resource inclusion, installed console entry point, and real MeerK40t backend before publication.
- Publishing cannot run if either test/build or clean-wheel verification fails, if the checksum manifest is missing, if the artifact bundle contains anything outside the exact wheel/sdist/manifest allowlist, or if any distribution digest differs in either the clean-wheel or publish job.

### Wave 6 — examples and documentation, only after behavioral gates pass

**Changes**
1. Regenerate the kraft-house manifest with relative artifact paths and document the actual machine-coordinate G-code envelope, including any flip/overhang.
2. Correct stale command sequences, DXF/export claims, directory paths, and test counts. Treat the root `skills/cli-anything-meerk40t/` tree as the canonical SKILL source and `cli_anything/meerk40t/skills/` as its generated snapshot; reconcile them together and never hand-edit the snapshot.
3. Replace hardcoded test counts with a reproducible command or omit them.
4. Document the upstream consoleserver trust boundary: it is unauthenticated and must remain loopback/firewall restricted; this client-side work does not make the upstream listener secure.
5. Update `README.md`, `MEERK40T.md`, `tests/TEST.md`, and both SKILL copies for every changed command/wire contract.

**Gate**
- Copy the example tree to an unrelated temporary directory and successfully preflight it there.
- Hermetic installed-wheel quick start: run every unattended quick-start command exactly as written from an unrelated temporary directory against the installed wheel, with no source-checkout path assumptions. Machine detection/probing, controller framing, movement, and burning are excluded from this set; every command in it must pass offline and unattended.
- Manual hardware smoke: detection/probe, controller check, live framing, movement, and burning run only under documented operator prerequisites and are never part of unattended CI or agent execution.
- A deterministic byte-for-byte check verifies the packaged `cli_anything/meerk40t/skills/` snapshot against the canonical root `skills/cli-anything-meerk40t/` tree, failing on any drift and identifying the mismatched file.

## Key load-bearing files

1. `cli_anything/meerk40t/meerk40t_cli.py` — `_complete_command`, CLI initialization precedence, project lifecycle commands, `_run_preflight`, attach client calls.
2. `cli_anything/meerk40t/utils/meerk40t_backend.py` — typed load/save postconditions and loader diagnostics.
3. `cli_anything/meerk40t/core/project.py` plus `core/session.py` — active-tree/path transaction and atomic restoration.
4. `cli_anything/meerk40t/utils/job_prep.py` plus `utils/materials.py` — strict schema, finite values, exhaustive role assignment, modal verifier, manifest generation.
5. `cli_anything/meerk40t/mk_control.py`, `utils/attach_client.py`, and `core/device.py` — staged-scene transaction, request correlation, provider lifecycle/state, and command acknowledgement.

Release/contribution boundary files are `.github/workflows/publish.yml`, `.github/workflows/profile-to-pr.yml`, and `utils/submit.py`; they are intentionally deferred until the runtime invariants are green.

## Verification sequence

Run focused tests after each wave, then the full documented gate once at the end:

```bash
CLI_ANYTHING_FORCE_INSTALLED=1 ./.venv/bin/python -m unittest \
  cli_anything.meerk40t.tests.test_core \
  cli_anything.meerk40t.tests.test_mk_plugin \
  cli_anything.meerk40t.tests.test_full_e2e -v
```

Final release rehearsal:

```bash
./.venv/bin/python -m build
./.venv/bin/python -m twine check dist/*
# Generate SHA256SUMS from the built distributions. In every downstream job,
# verify the downloaded files against SHA256SUMS before installation/upload.
# Then create a clean temporary venv, install only the digest-verified wheel, and run:
cli-anything-meerk40t --help
cli-anything-meerk40t --json machine list
CLI_ANYTHING_FORCE_INSTALLED=1 python -m unittest \
  cli_anything.meerk40t.tests.test_core \
  cli_anything.meerk40t.tests.test_mk_plugin \
  cli_anything.meerk40t.tests.test_full_e2e -v
```

The wheel test must consume tests/resources that are deliberately packaged or run an equivalent installed-package smoke suite; it must not accidentally import this checkout through the current working directory.

## Definition of done

- No user-facing mutation, load, save, stage, motion, or publish path reports success before its postcondition is observed.
- Failed project or stage transitions preserve both the prior bytes and the prior live scene.
- Safety preflight is derived from immutable artifact bytes plus trusted live/profile settings, not a mutable stored verdict.
- Physical commands require an explicit correlated acknowledgement; timeout is indeterminate.
- Release publication consumes distributions whose bytes match the SHA-256 manifest generated by the build job and independently verified by both clean-wheel and publish jobs; filenames or artifact names alone are never treated as identity. OIDC exists only at the final boundary.
- The complete real-backend suite and every new failure-path regression pass; docs/examples are verified from the installed artifact.
