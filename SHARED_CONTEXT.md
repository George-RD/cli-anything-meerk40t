# cli-anything-meerk40t — Boot Index

Orientation for any agent or contributor starting work in this repository.
Read this first; follow the links to the authoritative source.

## What this is
A stateful Click CLI + REPL that wraps the **real MeerK40t kernel** as its
backend for headless, agent-driven laser job preparation. The package lives in
`cli_anything/meerk40t/`; the repo root is the working directory.

## Start here (read in order)
1. [`README.md`](README.md) — what the tool does and how to install/run it.
2. [`MEERK40T.md`](MEERK40T.md) — the operator SOP for driving a real laser.
3. [`DESIGN.md`](DESIGN.md) — architecture, state model, and the 11 command groups.
4. [`BACKEND_CONTRACT.md`](BACKEND_CONTRACT.md) — the verified `Meerk40tBackend` interface.

## Decisions (why the code is shaped this way)
The `docs/decisions/` directory holds accepted decision records (ADRs). Each
maps a behavioural guarantee to the code that enforces it:

- [ADR-0001](docs/decisions/ADR-0001-command-outcome-autosave.md) — single structured command outcome + autosave boundary (issue #26)
- [ADR-0002](docs/decisions/ADR-0002-transactional-mutable-state.md) — transactional project/session state (issue #27)
- [ADR-0003](docs/decisions/ADR-0003-receiver-verified-artifacts.md) — receiver-verified job artifacts (issue #28)
- [ADR-0004](docs/decisions/ADR-0004-acknowledged-motion.md) — acknowledged GRBL motion (issue #29)
- [ADR-0005](docs/decisions/ADR-0005-build-once-publish.md) — build-once publish (issue #30)

## Plan of record
[`docs/plans/foundational-remediation.md`](docs/plans/foundational-remediation.md)
is the atomic-issue backlog that sequences this work.

## Testing
[`cli_anything/meerk40t/tests/TEST.md`](cli_anything/meerk40t/tests/TEST.md) —
durable test strategy and the load-bearing invariant suites. Run the suite with:

```bash
.venv/bin/python -m unittest cli_anything.meerk40t.tests.test_core -v
# or the full pytest sweep:
.venv/bin/python -m pytest cli_anything/meerk40t/tests/ -q
```

## Layout
```
cli_anything/meerk40t/
├── meerk40t_cli.py      # Click CLI + REPL, the completion boundary
├── core/                # project, session, elements, operations, device, export
├── utils/               # meerk40t_backend (real kernel), job_prep, materials, attach_client
├── skills/              # packaged agent skill (router + references)
├── tests/               # TEST.md, test_core.py, test_mk_plugin.py, test_full_e2e.py
└── mk_control.py        # consoleserver control channel (receiver staging)
docs/
├── plans/               # foundational-remediation.md (issue backlog)
└── decisions/           # ADR-*.md (this repo's design canon)
```
