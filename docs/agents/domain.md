# Domain docs

## Before exploring

Read:

- `CONTEXT.md` for the canonical project vocabulary.
- `SHARED_CONTEXT.md` for the repository boot index and authoritative documentation pointers.
- Relevant records under `docs/decisions/` before changing behavior in their area.

## Layout

This repository is single-context.

`CONTEXT.md` is a glossary only. Keep implementation plans and code structure out of it.

The existing `docs/decisions/` directory is the repository's ADR canon. This is a repo-local override of the upstream Matt Pocock default path `docs/adr/`. Do not create `docs/adr/`; add or revise decisions in `docs/decisions/` so there is one decision history.

## Vocabulary

Use the glossary's canonical terms in issues, specs, tests, and architecture proposals. If a needed domain concept is genuinely missing, update `CONTEXT.md` through the domain-modeling workflow rather than silently inventing a synonym.

If a proposal conflicts with an accepted decision, surface the conflict explicitly instead of silently overriding the ADR.
