# Roadmap

GitHub Issues are authoritative for work status and blocking relationships. This file is a navigation map for fresh agent sessions; do not duplicate ticket acceptance criteria here.

## Active architecture roadmap

Parent spec: [#58 — Align the harness with MeerK40t's native runtime seams](https://github.com/George-RD/cli-anything-meerk40t/issues/58)

```text
#59 Integration seam through shared status
  ↓
#60 Evidence-based compatibility matrix
  ├──→ #61 Native normal motion
  │       ↓
  │     #62 Controller-correlated GRBL jog/goto/frame
  │
  ├──→ #63 Planner/CutPlan semantic preparation
  │       ↓
  │     #64 Semantic manifests + verified artifact bytes
  │
  └──→ #65 Prove native tree snapshot staging

#64 + #65
   ↓
#66 Adopt proven production staging transaction

#62 + #64 + #66
        ↓
#67 Contract legacy MeerK40t coupling
        ↓
      close #58
```

### Ticket order and blockers

| Issue | Outcome | Blocked by |
| --- | --- | --- |
| #59 | Establish the canonical integration seam with a real shared headless/extension status workflow | None |
| #60 | Establish the supported MeerK40t compatibility contract and upstream-main warning lane | #59 |
| #61 | Route normal motion through native device/spooler/driver behavior | #60 |
| #62 | Correlate GRBL jog/goto/frame through MeerK40t controller ordering | #61 |
| #63 | Drive preparation through Planner/CutPlan/CutCode semantics | #60 |
| #64 | Bind manifests to semantic plan facts and independently verified bytes | #63 |
| #65 | Prove native tree snapshot/restore staging against the full failure matrix | #60 |
| #66 | Adopt the staging transaction proven by #65, preserving #64 verification semantics | #64, #65 |
| #67 | Remove obsolete direct coupling and finish the migration | #62, #64, #66 |

## How to select the next item

When asked to `/implement` without a specific issue, inspect open `ready-for-agent` tickets above and choose the first ticket whose `Blocked by` issues are all closed. If multiple branches are open, follow the table order unless the user gives a different priority. Always continue an existing branch/PR for that ticket before opening new work.

## Other open work

Issues #16, #17, and #18 are existing maintenance/upstream-watch items and are not part of parent spec #58. Do not treat them as blockers for this architecture roadmap unless a later ticket explicitly establishes a dependency.
