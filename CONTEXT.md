# CLI Anything MeerK40t

A command-line harness for preparing and checking laser work through the real MeerK40t kernel while keeping hardware action under operator control.

## Language

**Harness**:
The stateful CLI and REPL that expose MeerK40t capabilities to a human or agent while MeerK40t remains the underlying laser engine.
_Avoid_: replacement backend, reimplementation

**Project**:
The live MeerK40t element tree together with its SVG persistence target.
_Avoid_: session, prepared job

**Session**:
Persistent working metadata for a project, including history, undo/redo state, selected device, and the project SVG reference.
_Avoid_: project

**Machine profile**:
The named description of a supported laser setup, including the device family and physical machine properties used to interpret a job.
_Avoid_: live device

**Material profile**:
The named set of per-machine laser settings for material roles, with provenance showing whether those settings are tested or estimated.
_Avoid_: machine profile

**Role**:
The intended fabrication treatment assigned to artwork, currently `cut`, `score`, or `etch`.
_Avoid_: operation type

**Prepared job**:
The inspected, machine-and-material-resolved artifacts produced from source artwork before hardware action.
_Avoid_: project, session

**Manifest**:
The record that binds a prepared job to its artifacts, hashes, machine/material context, operation inventory, settings fingerprint, and preparation-time verification history.
_Avoid_: preflight result

**Preflight**:
Fresh verification of a prepared job from the artifact bytes and current trusted machine/material information. Stored verification in a manifest is history, not authority.
_Avoid_: manifest validation

**Attach**:
The loopback control path from the CLI to a running MeerK40t kernel used to inspect the live state and stage a prepared job.
_Avoid_: unattended execution

**Staging**:
The transactional replacement of the live MeerK40t scene with a receiver-verified prepared job. A refused or failed stage must preserve or restore the previous scene.
_Avoid_: loading

**Operator boundary**:
The point where offline preparation ends and physical machine action begins. Framing, calibration, and laser operation require an operator at the machine.
_Avoid_: autonomous burn
