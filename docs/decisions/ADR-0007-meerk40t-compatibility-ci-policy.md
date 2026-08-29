# ADR-0007: Released compatibility is required; upstream main is informational

- Status: Accepted
- Issue: #60
- Date: 2026-08-29

## Context
ADR-0006 concentrates MeerK40t internals, capability detection, and version
adaptation behind one harness integration seam. The package nevertheless still
needs an explicit runtime-support contract: a broad dependency declaration is
not evidence that every matching MeerK40t release preserves the private/native
seams the harness depends on.

Current upstream `main` is also useful as an early-warning target, but it is
unreleased code outside this repository's control. Treating it as a required
merge gate would let unrelated upstream churn block otherwise compatible
changes.

## Decision
- Released MeerK40t versions named in the supported compatibility contract are
  exact-pinned and exercised by required CI. A failure in a supported-release
  lane blocks the change.
- The canonical integration-seam tests are an explicit compatibility gate. The
  broader behavioral suite also runs for supported releases so the support
  claim cannot be green while existing harness guarantees are red.
- Current MeerK40t upstream `main` is exercised in a clearly named
  `upstream main (informational)` lane. That job is `continue-on-error` and is
  scheduled as well as run for repository changes; it warns about future
  upstream breakage but does not block merges.
- Support claims and the package lower bound are changed only from observed CI
  evidence. Candidate releases may be probed while establishing or revising the
  contract, but only the documented supported set remains a required matrix.
- Any compatibility adaptation belongs behind the canonical integration seam
  and should be capability-detected. A release that cannot be supported cleanly
  is recorded as an unsupported/gap result rather than causing distributed
  version checks across callers.
- Issue #18 remains the separate maintenance watch for upstream MeerK40t PRs
  #3249 and #3250. This compatibility policy does not duplicate that watch.

## Consequences
- A fresh implementation session can distinguish the releases it must preserve
  from historical probe evidence and from unreleased upstream behavior.
- Merge safety is fail-closed for the released support contract while upstream
  development remains useful as an early warning instead of an external veto.
- Raising the supported floor is a deliberate compatibility change backed by
  an exact test result and documented alongside the matrix.
