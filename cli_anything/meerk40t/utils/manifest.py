"""Strict shared manifest validator and JSON loader.

Used by job preparation and preflight to load and validate
``clia-job-manifest-v1`` documents. Standard library only.
"""

from __future__ import annotations

import json
import math
from typing import Any

SCHEMA = "clia-job-manifest-v1"
JOB_KINDS = ("job", "ladder")
OPERATION_KINDS = ("engrave", "cut", "etch")
FILES_REQUIRED = ("input_svg", "job_svg", "gcode")

_VERIFICATION_REQUIRED = (
    "all_passed",
    "header_ok",
    "travel_s0_ok",
    "burn_s_ok",
    "end_ok",
    "in_bounds",
    "no_unassigned",
    "s_values",
    "g1_s_values",
    "x_range",
    "y_range",
    "unassigned_elements",
)
_VERIFICATION_BOOL_KEYS = (
    "all_passed",
    "header_ok",
    "travel_s0_ok",
    "burn_s_ok",
    "end_ok",
    "in_bounds",
    "no_unassigned",
)
_ALLOWED_TOP_LEVEL = (
    "schema",
    "kind",
    "created",
    "machine",
    "material",
    "files",
    "operations",
    "estimated_roles",
    "settings_fingerprint",
    "verification",
)
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


class ManifestValidationError(Exception):
    """Raised when a manifest fails strict validation or JSON loading."""

    def __init__(self, errors: Any) -> None:
        self.errors = list(errors)

    def __str__(self) -> str:
        return "; ".join(str(e) for e in self.errors)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_hex(s: Any) -> bool:
    return isinstance(s, str) and len(s) > 0 and all(c in _HEX_DIGITS for c in s)


def _reject_constant(token: str) -> Any:
    raise ManifestValidationError([f"invalid numeric token in JSON: {token}"])


def _no_duplicate_pairs(pairs: list[tuple[Any, Any]]) -> dict:
    seen: set[Any] = set()
    for key, _ in pairs:
        if key in seen:
            raise ManifestValidationError([f"duplicate object key: {key!r}"])
        seen.add(key)
    return dict(pairs)


def strict_load_json(text: str) -> dict:
    """Parse JSON strictly into a top-level dict.

    Rejects NaN/Infinity/-Infinity, duplicate object keys, non-dict roots,
    and any malformed JSON by raising :class:`ManifestValidationError`.
    """
    try:
        parsed = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_no_duplicate_pairs,
        )
    except json.JSONDecodeError as exc:
        raise ManifestValidationError([f"invalid JSON: {exc}"]) from exc
    if not isinstance(parsed, dict):
        raise ManifestValidationError(
            [f"top-level JSON value must be an object, got {type(parsed).__name__}"]
        )
    return parsed


def _check_number_range(errors: list[str], field: str, v: Any, lo: float, hi: float) -> None:
    if not _is_number(v) or not math.isfinite(v) or not (lo <= v <= hi):
        errors.append(f"{field} must be a finite number in {lo}..{hi} (got {v!r})")


def validate_manifest(manifest: dict) -> None:
    """Validate a full ``clia-job-manifest-v1`` document.

    Raises :class:`ManifestValidationError` with a list of human-readable
    strings on any violation; returns ``None`` if valid.
    """
    errors: list[str] = []

    if not isinstance(manifest, dict):
        raise ManifestValidationError(["manifest must be a JSON object"])

    kind = manifest.get("kind")

    allowed = set(_ALLOWED_TOP_LEVEL)
    if kind == "ladder":
        allowed.add("role")
        allowed.add("powers")
    unknown = sorted(k for k in manifest if k not in allowed)
    if unknown:
        errors.append(f"unknown top-level key: {', '.join(unknown)}")

    schema = manifest.get("schema")
    if schema != SCHEMA:
        errors.append(f"schema must be {SCHEMA!r} (got {schema!r})")

    if kind not in JOB_KINDS:
        errors.append(f"kind must be 'job' or 'ladder' (got {kind!r})")

    for field in ("created", "machine"):
        val = manifest.get(field)
        if not isinstance(val, str) or val == "":
            errors.append(f"{field} must be a non-empty string")
    # material is required and non-empty for jobs; ladders genuinely have no
    # material (prepare_ladder writes ""), so an empty string is permitted
    # only for the ladder kind.
    material_val = manifest.get("material")
    if not isinstance(material_val, str):
        errors.append("material must be a string")
    elif kind != "ladder" and material_val == "":
        errors.append("material must be a non-empty string for kind 'job'")

    files = manifest.get("files")
    if not isinstance(files, dict):
        errors.append("files must be a dict")
    else:
        present = set(files)
        missing = sorted(set(FILES_REQUIRED) - present)
        extra = sorted(present - set(FILES_REQUIRED))
        if missing:
            errors.append(f"files missing keys: {', '.join(missing)}")
        if extra:
            errors.append(f"files has unexpected keys: {', '.join(extra)}")
        for name in FILES_REQUIRED:
            ent = files.get(name)
            if not isinstance(ent, dict):
                if name in files:
                    errors.append(f"files.{name} must be a dict")
                continue
            path = ent.get("path")
            if not isinstance(path, str) or path == "":
                errors.append(f"files.{name}.path must be a non-empty string")
            sha = ent.get("sha256")
            if not _is_hex(sha):
                errors.append(f"files.{name}.sha256 must be a non-empty hex string")

    ops = manifest.get("operations")
    if not isinstance(ops, list):
        errors.append("operations must be a list")
    else:
        for i, op in enumerate(ops):
            prefix = f"operations[{i}]"
            if not isinstance(op, dict):
                errors.append(f"{prefix} must be a dict")
                continue
            if kind == "job":
                role = op.get("role")
                if not isinstance(role, str) or role == "":
                    errors.append(f"{prefix}.role must be a non-empty string")
            op_kind = op.get("kind")
            if op_kind not in OPERATION_KINDS:
                errors.append(
                    f"{prefix}.kind must be one of engrave/cut/etch (got {op_kind!r})"
                )
            color = op.get("color")
            if not isinstance(color, str) or color == "":
                errors.append(f"{prefix}.color must be a non-empty string")
            passes = op.get("passes")
            if not _is_int(passes) or passes < 1:
                errors.append(f"{prefix}.passes must be an integer >= 1 (got {passes!r})")
            power = op.get("power")
            if not _is_int(power) or not (1 <= power <= 1000):
                errors.append(
                    f"{prefix}.power must be an integer in 1..1000 (got {power!r})"
                )
            speed = op.get("speed")
            if not _is_number(speed) or not math.isfinite(speed) or speed <= 0:
                errors.append(f"{prefix}.speed must be a finite number > 0 (got {speed!r})")
            elements = op.get("elements")
            if not _is_int(elements) or elements < 0:
                errors.append(
                    f"{prefix}.elements must be an integer >= 0 (got {elements!r})"
                )

    estimated_roles = manifest.get("estimated_roles")
    if not isinstance(estimated_roles, list) or not all(
        isinstance(x, str) for x in estimated_roles
    ):
        errors.append("estimated_roles must be a list of strings")

    settings_fingerprint = manifest.get("settings_fingerprint")
    if kind == "job":
        if not isinstance(settings_fingerprint, str) or settings_fingerprint == "":
            errors.append("settings_fingerprint must be a non-empty string for kind 'job'")
    elif kind == "ladder":
        if settings_fingerprint is not None and (
            not isinstance(settings_fingerprint, str) or settings_fingerprint == ""
        ):
            errors.append("settings_fingerprint must be null for kind 'ladder'")

    verification = manifest.get("verification")
    if not isinstance(verification, dict):
        errors.append("verification must be a dict")
    else:
        missing = sorted(set(_VERIFICATION_REQUIRED) - set(verification))
        if missing:
            errors.append(f"verification missing keys: {', '.join(missing)}")
        for key in _VERIFICATION_BOOL_KEYS:
            val = verification.get(key)
            if not isinstance(val, bool):
                errors.append(f"verification.{key} must be a bool")
        s_values = verification.get("s_values")
        if not (isinstance(s_values, list) and all(_is_int(x) for x in s_values)):
            errors.append("verification.s_values must be a list of ints")
        g1_s_values = verification.get("g1_s_values")
        if not (isinstance(g1_s_values, list) and all(_is_int(x) for x in g1_s_values)):
            errors.append("verification.g1_s_values must be a list of ints")
        for rng_name in ("x_range", "y_range"):
            rng = verification.get(rng_name)
            if not (
                isinstance(rng, list)
                and len(rng) == 2
                and all(_is_number(x) and math.isfinite(x) for x in rng)
            ):
                errors.append(
                    f"verification.{rng_name} must be a list of exactly 2 finite numbers"
                )
        unassigned = verification.get("unassigned_elements")
        if not isinstance(unassigned, list):
            errors.append("verification.unassigned_elements must be a list")

    if kind == "ladder":
        role = manifest.get("role")
        if not isinstance(role, str) or role == "":
            errors.append("role must be a non-empty string for kind 'ladder'")
        powers = manifest.get("powers")
        if not (
            isinstance(powers, list)
            and all(_is_int(p) and 1 <= p <= 1000 for p in powers)
        ):
            errors.append("powers must be a list of ints in 1..1000 for kind 'ladder'")

    if errors:
        raise ManifestValidationError(errors)
