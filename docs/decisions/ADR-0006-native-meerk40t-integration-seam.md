# ADR-0006: Native MeerK40t integration is the canonical seam

- Status: Accepted
- Issue: #58
- Date: 2026-08-29

## Context
The harness currently reaches MeerK40t through several mechanisms: console commands, direct services, controller/channel access, live-tree manipulation, and compatibility patches. A fresh review of upstream MeerK40t shows that much of the behavior we need already has native ownership in the Elements tree, Planner/CutPlan/CutCode, active device service, Spooler, Driver, and Controller. Keeping direct knowledge of those internals distributed across the harness makes correctness and compatibility harder to reason about.

## Decision
- The harness will have one canonical MeerK40t integration seam. Direct access to MeerK40t internals, capability detection, and supported-version adaptation belongs behind that seam.
- Native MeerK40t services/objects are preferred when they provide the needed behavior. Console execution remains a contained adapter where upstream exposes no suitable native route; it is not the default architecture.
- Headless use and the installed `meerk40t.extension` path must delegate to the same harness workflows. The extension remains lifecycle/transport glue rather than a second implementation of safety or job logic.
- MeerK40t owns device/runtime ordering. The acknowledged-motion invariant from ADR-0004 remains, but raw receive-channel observation is no longer the canonical correlation mechanism. Motion should use the active device/spooler/driver/controller semantics, including controller-owned command ordering for any GRBL-specific `$J` path.
- MeerK40t Planner/CutPlan/CutCode is the semantic source during preparation where it already owns the information. Exact emitted artifact bytes remain the safety authority: hashes, modal G-code verification, current provenance checks, and receiver-side verification remain independent requirements under ADR-0003.
- Staging remains transactional, but its permanent implementation is not decided here. Native tree `backup_tree`/`restore_tree` and isolated/shadow-load approaches must be proven against the staging failure matrix before replacing current rollback logic.
- MeerK40t compatibility is evidence-based, not an open-ended promise implied by a broad dependency range. Supported releases are explicitly tested at this seam; upstream main is used as an early-warning target. Small generally useful missing seams should be considered for upstream contribution, with capability-detected local adapters allowed so the harness does not block on upstream acceptance.

## Consequences
- Upstream churn should concentrate in one module instead of causing shotgun surgery across workflows.
- Tests can exercise one harness-facing integration interface while still using the real MeerK40t kernel and native GRBL emulator where practical.
- ADR-0004's requirement that motion never reports false success remains binding; only its current raw-channel correlation mechanism is superseded.
- ADR-0003 remains fully binding: semantic planning data can strengthen preparation but can never replace fresh verification of the exact staged/emitted bytes.
- The first repo-only architecture recommendation to deepen staging first is superseded by the sequence in #58: integration seam, compatibility contract, motion/planning migration, then a proven staging transaction.
