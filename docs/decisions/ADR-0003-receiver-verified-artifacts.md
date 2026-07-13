# ADR-0003: Receiver-verified job artifacts (manifest + staging)

- Status: Accepted
- Issue: #28
- Date: 2026-07-14

## Context
A prepared job is a bundle (job SVG, G-code, manifest). The sender must record
enough evidence that the receiver can independently verify the artifact before
trusting it — and reject tampering or mis-staged jobs.

## Decision
- `prepare_job` (`job_prep.py:343`) resolves material/machine settings, builds
  ops, exports the job SVG and G-code, and verifies the G-code structurally via
  `_verify_gcode_file` (`job_prep.py:262`).
- It writes a `clia-job-manifest-v1` JSON (`job_prep.py:314`) containing, per
  output file, a `sha256` digest (`job_prep.py:320`); a `settings_fingerprint`
  (sha256 of the resolved settings, `job_prep.py:338`, `:325`); and a
  `verification` block (`header_ok`, `travel_s0_ok`, `burn_s_ok`, `end_ok`,
  `in_bounds`, `no_unassigned`, `all_passed`) (`job_prep.py:431-438`).
- The CLI re-verifies via `_run_preflight` (`meerk40t_cli.py:1517`): it
  recomputes the file hashes and settings fingerprint and **re-derives the
  provenance gate from the material store**, not the manifest's `estimated_roles`
  field (`meerk40t_cli.py:1927`, `:2097`), so an edited manifest cannot hide an
  estimated role.
- The receiver, `mk_control._stage_file` (`mk_control.py:166`), re-hashes the
  staged file **before touching the scene** (`mk_control.py:180-189`); on a hash
  mismatch it returns an error frame and leaves the scene byte-for-byte unchanged
  (`mk_control.py:183-189`). It then replaces the scene with exactly the staged
  job, removing the pre-existing node snapshot to prevent accumulation of a
  previous job's operations (`mk_control.py:191-208`).
- G-code export refuses any operation still at the default power of 1000 unless
  `allow_full_power` is set (`export.py:127-144`).

## Consequences
- Tampered G-code is rejected by both the CLI preflight and the receiver.
- Staged jobs are hash-verified; the live scene never silently accumulates.
- The manifest is evidence, not authority: the gate is re-derived server-side.
