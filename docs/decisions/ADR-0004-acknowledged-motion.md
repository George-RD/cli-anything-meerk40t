# ADR-0004: Acknowledged motion (GRBL jog / goto / frame)

- Status: Superseded in implementation mechanism by ADR-0006; invariant remains Accepted
- Issue: #29
- Date: 2026-07-14

## Context
Motion commands must never assume success. A move that the controller rejects
(or to which no hardware replies) must be reported as a failure, never as a
silent "moved".

## Decision
- `jog` / `goto` / `frame` gate on a live writable device connection and return a structured failure rather than a traceback when the device/link is unavailable.
- GRBL-specific jog behavior uses the GRBL 1.1 jogging command where required.
- A move must be correlated with the controller response for that exact command. **An empty or indeterminate reply is never success.**
- Each move result carries structured acknowledgement/error information suitable for agent branching.
- Device preflight continues to verify relevant GRBL safety/configuration state and reports failures as structured outcomes.

ADR-0006 supersedes the original implementation choice of independently watching the raw GRBL receive channel and pinning the harness to its own correlation mechanism. The acknowledged-motion invariant remains binding, but correlation and ordering should now be owned through MeerK40t's native device/controller/spooler integration seam.

## Consequences
- An agent never receives a false "moved" signal.
- Connection refusal, indeterminate completion, and hardware rejection are explicit structured outcomes.
- Tests should pin observable acknowledgement behavior rather than a duplicated internal transport implementation.
