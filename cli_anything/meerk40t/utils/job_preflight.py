"""Shared, bytes-oriented job-artifact verification.

This is the single source of truth for re-verifying a prepared job manifest.
Both the CLI ``job preflight`` / ``attach stage`` commands (which read bytes from
disk) and the attach receiver (which receives bytes over the wire) call
:func:`verify_job_artefacts` so a staging refusal uses exactly the same logic as
a local preflight. No second schema/verification implementation may exist — the
manifest schema lives in :mod:`cli_anything.meerk40t.utils.manifest`.

Every safety fact is derived FRESH from the bytes passed in (or re-resolved from
the trusted material store), never from a stored verdict.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from cli_anything.meerk40t.utils.manifest import (
    validate_manifest,
    strict_load_json,
    ManifestValidationError,
)
from cli_anything.meerk40t.utils import materials as materials_mod
from cli_anything.meerk40t.utils.profiles import load_profile
from cli_anything.meerk40t.utils.gcode_modal import verify_gcode
from cli_anything.meerk40t.utils.job_prep import _load_machine_bed


def verify_job_artefacts(
    manifest: dict[str, Any],
    *,
    file_bytes: Optional[dict[str, bytes]] = None,
    allow_estimated: bool = False,
    stage_mode: bool = False,
) -> tuple[dict[str, Any], int]:
    """Re-verify a job manifest from already-loaded bytes.

    ``file_bytes`` maps logical file names (``input_svg``, ``job_svg``,
    ``gcode``) to the raw bytes the caller has in hand. A name present in
    ``file_bytes`` is hashed against the manifest-recorded sha256; a name absent
    is skipped (the caller is responsible for files it does not hold). Only
    ``gcode`` triggers the G-code safety recompute.

    Returns ``(result, exit_code)``. Hard failures (hash mismatch, changed
    material settings, failed verification, invalid manifest) are exit 1 for a
    preflight and exit 2 for a staging gate. The estimated-role gate always
    forces exit 2 when it fires, even in preflight mode.
    """
    file_bytes = file_bytes or {}
    try:
        validate_manifest(manifest)
    except ManifestValidationError as exc:
        return (
            {
                "ok": False,
                "failures": ["manifest validation failed: " + "; ".join(exc.errors)],
            },
            2 if stage_mode else 1,
        )

    is_ladder = manifest.get("kind", "job") == "ladder"
    machine = manifest.get("machine")
    material = manifest.get("material")
    failures: list[str] = []
    # For staging, g-code MUST be carried in the envelope; the receiver refuses
    # to trust the manifest's stored modal-safety verdict. A staged request
    # without g-code is rejected before any artifact is loaded.
    if stage_mode and not file_bytes.get("gcode"):
        failures.append(
            "staging requires g-code bytes in the envelope (manifest modal-safety is not trusted)"
        )
    warnings: list[str] = []

    # 1. Hash every file the caller actually holds against the recorded sha256.
    for fname in ("input_svg", "job_svg", "gcode"):
        if fname not in file_bytes:
            continue
        entry = (manifest.get("files") or {}).get(fname) or {}
        recorded = entry.get("sha256")
        actual = hashlib.sha256(file_bytes[fname]).hexdigest()
        if recorded is None:
            failures.append(f"manifest missing files.{fname}.sha256")
        elif actual != recorded:
            failures.append(f"{fname} hash mismatch against received bytes")

    # 2. Re-resolve material + machine and recompute the settings fingerprint.
    #    Ladder manifests have no settings_fingerprint to check.
    if not is_ladder:
        fingerprint = manifest.get("settings_fingerprint")
        try:
            mat = materials_mod.load_material(material) if material else None
            if mat is None:
                failures.append(
                    f"material {material!r} no longer available - re-run job prepare"
                )
            else:
                roles = materials_mod.resolve_settings(mat, machine)
                payload = json.dumps(roles, sort_keys=True)
                actual_fp = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                if actual_fp != fingerprint:
                    failures.append(
                        "material settings changed since prepare - re-run job prepare"
                    )
        except (ValueError, materials_mod.MaterialError) as exc:
            failures.append(f"{exc} - re-run job prepare")

    # 3. RECOMPUTE the g-code safety verdict from the recorded bytes. The stored
    #    verification block is HISTORY only. This runs only when the caller holds
    #    the g-code bytes (the CLI path); the receiver verifies the SVG hash and
    #    leaves g-code to the local preflight gate.
    verification = manifest.get("verification", {})
    kind = manifest.get("kind", "job")
    if "gcode" in file_bytes:
        try:
            bed_width, bed_height = _load_machine_bed(machine)
            recomputed = verify_gcode(
                file_bytes["gcode"].decode("utf-8", "replace"),
                bed_width=bed_width,
                bed_height=bed_height,
                valid_burn_s={
                    op["power"]
                    for op in manifest.get("operations", [])
                    if isinstance(op, dict) and op.get("power")
                },
                expected_passes=(
                    len(manifest["powers"]) if kind == "ladder" else None
                ),
                is_ladder=(kind == "ladder"),
            )
            recomputed_no_unassigned = (
                len(verification.get("unassigned_elements", [])) == 0
            )
            if not (recomputed["all_passed"] and recomputed_no_unassigned):
                msg = "recomputed verification failed: " + "; ".join(
                    recomputed["notes"]
                )
                if not recomputed_no_unassigned:
                    msg += "; unassigned elements present"
                failures.append(msg)
        except Exception as exc:  # pragma: no cover - defensive
            failures.append(f"could not recompute verification: {exc}")

    # 4. Estimated-role gate - provenance comes from the TRUSTED material store,
    #    never from the manifest's recorded estimated_roles. A disagreement with
    #    the recorded estimated_roles means the manifest was tampered with.
    #    Ladder manifests carry no roles, so the gate does not apply.
    reevaluated_estimated: set[str] = set()
    forced_code: Optional[int] = None
    if not is_ladder:
        ops = manifest.get("operations", []) or []
        present_roles: Optional[set[str]] = {
            op["role"]
            for op in ops
            if isinstance(op, dict) and op.get("role")
        }
        if not present_roles:
            present_roles = None
        recorded_estimated = set(manifest.get("estimated_roles", []) or [])
        try:
            mat_gate = materials_mod.load_material(material) if material else None
        except (ValueError, materials_mod.MaterialError):
            mat_gate = None
        if mat_gate is not None:
            try:
                resolved = materials_mod.resolve_settings(mat_gate, machine)
            except ValueError:
                resolved = None
            if resolved is not None:
                for role, rset in resolved.items():
                    if present_roles is not None and role not in present_roles:
                        continue
                    if rset.get("provenance") != "tested":
                        reevaluated_estimated.add(role)
                if reevaluated_estimated != recorded_estimated:
                    return (
                        {
                            "ok": False,
                            "failures": [
                                "manifest estimated_roles tampered: recorded "
                                f"{sorted(recorded_estimated)} but the material store "
                                f"resolves {sorted(reevaluated_estimated)}"
                            ],
                        },
                        2 if stage_mode else 1,
                    )
    reevaluated_estimated_list = sorted(reevaluated_estimated)
    if reevaluated_estimated_list and not allow_estimated:
        failures.append(
            f"estimated roles {reevaluated_estimated_list} require --allow-estimated"
        )
        forced_code = 2

    if failures:
        return (
            {
                "ok": False,
                "failures": failures,
                "estimated_roles": reevaluated_estimated_list,
            },
            forced_code if forced_code is not None else (2 if stage_mode else 1),
        )

    return (
        {
            "ok": True,
            "estimated_roles": reevaluated_estimated_list,
            "operations": manifest.get("operations", []),
            "warnings": warnings,
        },
        0,
    )
