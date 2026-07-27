# Product context

## Product

`cli-anything-meerk40t` is an agent CLI harness for MeerK40t laser software. It exposes the real MeerK40t kernel through a stateful CLI and REPL so an AI agent or a human can prepare, inspect, export, and preflight a laser job without replacing MeerK40t itself.

## Audience

- Developers building agent workflows around physical fabrication.
- MeerK40t users who want repeatable terminal automation.
- Operators who need a clear boundary between offline preparation and hardware action.

## Core mechanism

A design moves through explicit states: source SVG → classified operations → material-resolved job → exported artifacts and manifest → integrity preflight → operator-controlled hardware action.

## Product truths

- The harness drives the real MeerK40t kernel.
- Offline preparation and preflight do not need a connected laser.
- Estimated material settings are inspection-only, not burn-ready.
- A real job requires scrap calibration, recorded settings, and an operator present for hardware actions.
- The MeerK40t console server is unauthenticated and must remain loopback-bound and firewall-restricted.
- The package is MIT licensed and part of the CLI-Anything ecosystem.

## Primary site action

Install the package or inspect the source repository. The page must communicate the operator boundary before a visitor could mistake automation for unattended laser operation.
