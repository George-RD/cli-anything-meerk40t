# Matt Pocock engineering workflow

Vendored from `mattpocock/skills` at upstream commit `6654f6b60cd9d5be8b54c6fafe44346dabeb3b76` (2026-08-24).

This repo installs the engineering flow it uses directly:

- `setup-matt-pocock-skills`
- `grill-with-docs`
- `grilling` (dependency)
- `domain-modeling`
- `to-spec`
- `to-tickets`
- `implement`
- `tdd`
- `code-review`
- `codebase-design`
- `improve-codebase-architecture`

The skill text is vendored so agents can use the workflow without depending on a mutable global install. Repo-specific initialization lives in `AGENTS.md` and `docs/agents/`.

When refreshing, compare against the pinned upstream commit first and preserve this repository's explicit `docs/decisions/` ADR-path override.
