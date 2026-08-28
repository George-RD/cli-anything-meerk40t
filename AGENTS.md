# Agent instructions

Read `CONTEXT.md`, `SHARED_CONTEXT.md`, and the relevant records in `docs/decisions/` before changing behavior.

## Agent skills

### Issue tracker

Work is tracked in GitHub Issues for `George-RD/cli-anything-meerk40t`. See `docs/agents/issue-tracker.md`.

### Triage labels

Agent-ready work uses the canonical Matt Pocock state vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`) when those labels are available. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. The glossary is `CONTEXT.md`. The existing ADR canon is `docs/decisions/`; do not create a competing `docs/adr/` tree. See `docs/agents/domain.md`.

## Development workflow

Use the vendored Matt Pocock engineering workflow under `.agents/skills/`:

1. Start new or ambiguous work with `/grill-with-docs` so terminology and durable decisions are captured while the idea is sharpened.
2. For work larger than one focused implementation session, use `/to-spec`, then `/to-tickets` to create tracer-bullet issues with explicit blocking edges.
3. Use `/implement` for implementation. It should drive `/tdd` at agreed seams, run the relevant checks during the work, run the full test suite at the end, then use `/code-review` before committing.
4. Use `/improve-codebase-architecture` periodically to find deepening opportunities before architecture friction becomes feature work.

If the user has already provided a settled issue/spec, enter the workflow at the closest applicable step rather than repeating earlier phases.

## Repository invariants

- The real MeerK40t kernel remains the backend; do not replace it with a parallel implementation.
- Offline preparation and verification stay separate from operator-controlled hardware action.
- Preserve accepted ADR guarantees unless the task explicitly reopens the decision.
- In particular, keep exactly-once command outcome/persistence semantics, receiver-side verification of staged artifacts, acknowledged motion, and build-once publishing.
