# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues. Use the `gh` CLI for operations when working in a local checkout; connected GitHub tooling may perform the equivalent operations when available.

## Conventions

- Create one issue per durable work item.
- Read the full issue body and comments before implementation.
- Use native GitHub blocking/dependency relationships where available; otherwise put `Blocked by: #<n>` in the issue body.
- Work the unblocked frontier first.
- Pull requests are an implementation/review surface, not the request-triage surface by default.
- A spec published by `/to-spec` or a tracer-bullet ticket published by `/to-tickets` should be marked `ready-for-agent` when that label exists.

## Wayfinding-compatible relationships

For larger work, a map issue may own child issues. Child issues should use GitHub sub-issues where available and native issue dependencies for blockers. If those features are unavailable, preserve the same relationships explicitly in the issue body rather than dropping them.
