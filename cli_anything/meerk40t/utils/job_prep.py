"""Laser job preparation and calibration ladder generation.

Material-profile-driven preparation: operation settings (cut/score/etch) are
resolved from a named material and machine profile, with a provenance gate
that refuses untested settings unless explicitly allowed. Ladders generate the
scrap test patterns used to calibrate a material.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from cli_anything.meerk40t.core import export, operations
from cli_anything.meerk40t.utils.materials import (
    load_material,
    MaterialError,
    resolve_settings as resolve_material_settings,
)
from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend
from cli_anything.meerk40t.utils.profiles import load_profile
from cli_anything.meerk40t.utils.gcode_modal import verify_gcode
from cli_anything.meerk40t.utils.manifest import (
    validate_manifest,
    strict_load_json,
    ManifestValidationError,
)

DEFAULT_COLOR_MAP = {"#ff0000": "cut", "#0000ff": "score", "#000000": "etch"}
BURN_ORDER = ["etch", "score", "cut"]


class JobPrepError(Exception):
    """Base class for errors raised during job preparation."""


class MissingRoleError(JobPrepError):
    """A required role is missing from the material for this machine."""


class UncalibratedSettingsError(JobPrepError):
    """One or more settings have not been tested and the gate was not opened."""

    def __init__(self, message: str, estimated_roles: list[str]) -> None:
        super().__init__(message)
        self.estimated_roles = estimated_roles


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _dim_mm(value: str | float | int | None) -> float:
    """Best-effort parse of a bed dimension string such as ``410mm`` into mm."""
    if value is None:
        raise ValueError("bed dimension is None")
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    m = re.match(r"(-?[\d.]+)\s*mm?", s, re.IGNORECASE)
    if m:
        return float(m.group(1))
    raise ValueError(f"cannot parse bed dimension: {value!r}")


def _load_machine_bed(machine: str) -> tuple[float, float]:
    profile = load_profile(machine)
    if profile is None:
        raise ValueError(f"unknown machine profile: {machine!r}")
    return _dim_mm(profile.get("bedwidth")), _dim_mm(profile.get("bedheight"))


def _resolve_role_settings(
    material_name: str, machine: str, config_home: Optional[str]
) -> dict[str, dict]:
    try:
        material = load_material(material_name, config_home=config_home)
    except MaterialError as exc:
        raise JobPrepError(str(exc)) from exc
    if material is None:
        raise ValueError(f"unknown material: {material_name!r}")
    return resolve_material_settings(material, machine)


def _color_for_role(role: str, color_map: dict[str, str]) -> str:
    for color, mapped_role in color_map.items():
        if mapped_role == role:
            return color
    raise KeyError(f"no colour mapped to role {role!r}")


def _build_ops(
    backend: Meerk40tBackend, settings: dict[str, dict], color_map: dict[str, str]
) -> list[Any]:
    """Create operations in fixed burn order and return them."""
    operations.clear_operations(backend)
    for role in BURN_ORDER:
        if role not in settings:
            continue
        s = settings[role]
        color = _color_for_role(role, color_map)
        backend.run(
            f"{s['kind']} --color {color} "
            f"--speed {s['speed']} --power {s['power']} --passes {s['passes']}"
        )
    return backend.ops()

def _validate_settings_values(settings: dict[str, dict]) -> None:
    """Reject out-of-range resolved role values before any kernel work.

    Mirrors the material-record invariants enforced at the CLI: power is an
    integer in 1..1000 inclusive, speed is strictly positive, passes is an
    integer >= 1. A hand-edited material JSON must not reach the laser with a
    value that would silently mis-drive the machine.
    """
    for role, s in settings.items():
        power = s.get("power")
        if (
            not isinstance(power, int)
            or isinstance(power, bool)
            or power < 1
            or power > 1000
        ):
            raise JobPrepError(
                f"role {role!r} power {power!r} is outside the valid range 1..1000"
            )
        speed = s.get("speed")
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or speed <= 0:
            raise JobPrepError(
                f"role {role!r} speed {speed!r} must be greater than zero"
            )
        passes = s.get("passes")
        if not isinstance(passes, int) or isinstance(passes, bool) or passes < 1:
            raise JobPrepError(
                f"role {role!r} passes {passes!r} must be an integer >= 1"
            )


def _assign_elements_by_color(backend: Meerk40tBackend) -> tuple[dict[str, int], list[str]]:
    """Assign every loaded element to the operation matching its stroke colour.

    Returns (counts by colour, descriptions of unassigned drawable elements).
    Elements with neither a real stroke nor a real fill (containers, structural
    nodes) are skipped. A drawable element whose stroke has no matching operation
    is reported, AND a fill-only element (no mappable stroke but a real fill) is
    reported too, so the job cannot silently drop artwork that lives only in a
    fill colour.
    """
    ops = backend.ops()
    color_to_op: dict[str, Any] = {}
    for op in ops:
        color = str(getattr(op, "color", "")).lower()
        if color and color not in ("none", ""):
            color_to_op[color] = op
    counts: dict[str, int] = {}
    unassigned: list[str] = []
    for e in backend.elems():
        raw_stroke = getattr(e, "stroke", None)
        stroke = str(raw_stroke or "").lower()
        op = color_to_op.get(stroke)
        if op is not None:
            op.add_reference(e)
            counts[stroke] = counts.get(stroke, 0) + 1
            continue
        # No operation mapped to this stroke. Flag any drawable element: one
        # with a real unmatched stroke OR a real fill (fill-only artwork).
        raw_fill = getattr(e, "fill", None)
        fill = str(raw_fill or "").lower()
        has_stroke = raw_stroke is not None and stroke not in ("", "none")
        has_fill = raw_fill is not None and fill not in ("", "none")
        if has_stroke:
            unassigned.append(f"{getattr(e, 'type', '?')} stroke={stroke}")
        elif has_fill:
            unassigned.append(f"{getattr(e, 'type', '?')} fill={fill}")
    return counts, unassigned


def _set_bed_and_realize(backend: Meerk40tBackend, machine: str) -> None:
    """Set the active GRBL device's bed size from the machine profile and realise it."""
    bed_w, bed_h = _load_machine_bed(machine)
    dev = backend.device()
    if dev is None:
        raise RuntimeError("No GRBL device is active")
    dev.bedwidth = f"{bed_w}mm"
    dev.bedheight = f"{bed_h}mm"
    dev.realize()


def _human_summary(summary: dict[str, Any]) -> str:
    lines = ["MeerK40t job preparation complete", ""]
    lines.append(f"Input:  {summary['input']}")
    lines.append(f"Output: {summary['job_svg']}")
    lines.append(f"G-code: {summary['gcode']}")
    lines.append("")
    lines.append("Operations:")
    for op in summary["operations"]:
        lines.append(
            f"  {op['color']} -> {op['kind']:8} passes={op['passes']} "
            f"power={op['power']} speed={op['speed']} mm/s "
            f"elements={op['elements']}"
        )
    lines.append("")
    lines.append("G-code verification:")
    for key, val in summary["verification"].items():
        status = "PASS" if val is True else ("FAIL" if val is False else "")
        if status:
            lines.append(f"  [{status}] {key}: {val}")
        else:
            lines.append(f"  {key}: {val}")
    return "\n".join(lines)


def _write_manifest(
    out_dir: Path,
    stem: str,
    *,
    machine: str,
    material: str,
    files: dict[str, dict[str, str]],
    operations: list[dict],
    estimated_roles: list[str],
    settings_fingerprint: str | None,
    verification: dict,
    kind: str = "job",
    role: str | None = None,
    powers: list[int] | None = None,
) -> Path:
    """Write a clia-job-manifest-v1 JSON file and return its path.

    ``files`` is a precomputed mapping of slot -> {"path": relpath, "sha256": hex}
    captured once at prepare time; the manifest is written directly from it
    (never re-read from disk) so the recorded hashes describe the exact bytes
    that were verified.
    """
    manifest_path = out_dir / f"{stem}_manifest.json"
    manifest: dict[str, Any] = {
        "schema": "clia-job-manifest-v1",
        "kind": kind,
        "created": datetime.now(timezone.utc).isoformat(),
        "machine": machine,
        "material": material,
        "files": {
            name: {"path": entry["path"], "sha256": entry["sha256"]}
            for name, entry in files.items()
        },
        "operations": operations,
        "estimated_roles": list(estimated_roles),
        "settings_fingerprint": settings_fingerprint,
        "verification": verification,
    }
    if role is not None:
        manifest["role"] = role
    if powers is not None:
        manifest["powers"] = list(powers)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    return manifest_path


def _settings_fingerprint(settings: dict[str, dict]) -> str:
    payload = json.dumps(settings, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def prepare_job(
    input_svg: str,
    out_dir: str,
    *,
    machine: str,
    material: str,
    color_map: Optional[dict[str, str]] = None,
    allow_estimated: bool = False,
    config_home: Optional[str] = None,
) -> dict[str, Any]:
    """Load input_svg, build material-driven ops, export job SVG and G-code, verify."""
    color_map = color_map if color_map is not None else DEFAULT_COLOR_MAP

    # Resolve material and machine settings before starting the kernel.
    settings = _resolve_role_settings(material, machine, config_home)
    bed_width, bed_height = _load_machine_bed(machine)

    for color, role in color_map.items():
        if role not in settings:
            raise MissingRoleError(
                f"material {material!r} has no {role!r} settings for machine {machine!r}; "
                "run 'job ladder' on scrap, then 'materials record'"
            )
    # Finding 9: a custom colour map names exactly the roles the operator wants
    # processed. Drop any resolved role the map does not name BEFORE fingerprinting,
    # deriving estimated roles, or building operations, so a three-role material
    # used with `--map #ff0000=cut` yields only the cut op (and no KeyError).
    # Keep the full resolution for the settings fingerprint so preflight (which
    # re-resolves every role) computes the same hash regardless of the map.
    full_settings = settings
    mapped_roles = set(color_map.values())
    settings = {role: s for role, s in settings.items() if role in mapped_roles}

    # Finding 5: reject out-of-range resolved values before booting the kernel so
    # a hand-edited material JSON cannot produce a mis-driven job.
    _validate_settings_values(settings)


    estimated = [role for role, s in settings.items() if s.get("provenance") != "tested"]
    if estimated and not allow_estimated:
        raise UncalibratedSettingsError(
            f"untested settings for roles {estimated}; run a calibration ladder on scrap "
            "first (see materials.md), record it with 'materials record', or pass --allow-estimated",
            estimated_roles=estimated,
        )

    in_path = Path(input_svg).resolve()
    out_path = Path(out_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)
    stem = in_path.stem
    job_svg_path = out_path / f"{stem}_job.svg"
    gcode_path = out_path / f"{stem}.gcode"

    if not in_path.exists():
        raise FileNotFoundError(f"Input SVG not found: {in_path}")

    backend = Meerk40tBackend(device="grbl")
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        backend.start()
        try:
            backend.run("service device start -i grbl 0")
            _set_bed_and_realize(backend, machine)
            backend.load_file(str(in_path))
            _build_ops(backend, settings, color_map)
            element_counts, unassigned = _assign_elements_by_color(backend)
            backend.save_svg(str(job_svg_path))
            gcode_result = export.export_gcode(
                backend, str(gcode_path), allow_full_power=True
            )
        finally:
            backend.shutdown()

    if gcode_result.get("error"):
        raise RuntimeError(f"G-code export failed: {gcode_result['error']}")
    if not os.path.exists(gcode_path) or os.path.getsize(gcode_path) == 0:
        raise RuntimeError("G-code export produced no output file")

    # Capture each output buffer EXACTLY ONCE. The verification verdict and the
    # manifest hashes are computed from these captured bytes and never re-read
    # from disk afterwards, so the recorded hashes describe the exact bytes that
    # were verified (defeats a post-capture source swap).
    gcode_bytes = Path(gcode_path).read_bytes()
    sha_gcode = hashlib.sha256(gcode_bytes).hexdigest()
    input_bytes = Path(in_path).read_bytes()
    sha_input = hashlib.sha256(input_bytes).hexdigest()
    job_bytes = Path(job_svg_path).read_bytes()
    sha_job = hashlib.sha256(job_bytes).hexdigest()

    valid_powers = {s["power"] for s in settings.values() if s.get("power")}
    verification = verify_gcode(
        gcode_bytes.decode("utf-8", "replace"),
        bed_width=bed_width,
        bed_height=bed_height,
        valid_burn_s=valid_powers,
        expected_passes=None,
        is_ladder=False,
    )
    # Derive the unassigned-element verdict from the per-element assignment
    # performed above; this MUST be recomputed here, never copied from a stored
    # value. Fill-only and unmatched-stroke artwork both fail the gate.
    verification["unassigned_elements"] = unassigned
    verification["no_unassigned"] = len(unassigned) == 0
    verification["all_passed"] = bool(
        verification["all_passed"] and verification["no_unassigned"]
    )

    op_summary = []
    for role in BURN_ORDER:
        if role not in settings:
            continue
        s = settings[role]
        color = _color_for_role(role, color_map)
        op_summary.append(
            {
                "role": role,
                "kind": s["kind"],
                "color": color,
                "passes": s["passes"],
                "power": s["power"],
                "speed": s["speed"],
                "elements": element_counts.get(color.lower(), 0),
            }
        )

    manifest_path = _write_manifest(
        out_path,
        stem,
        machine=machine,
        material=material,
        files={
            "input_svg": {
                "path": os.path.relpath(str(in_path), out_path),
                "sha256": sha_input,
            },
            "job_svg": {
                "path": os.path.relpath(str(job_svg_path), out_path),
                "sha256": sha_job,
            },
            "gcode": {
                "path": os.path.relpath(str(gcode_path), out_path),
                "sha256": sha_gcode,
            },
        },
        operations=op_summary,
        estimated_roles=estimated,
        settings_fingerprint=_settings_fingerprint(full_settings),
        verification=verification,
    )

    summary = {
        "input": str(in_path),
        "job_svg": str(job_svg_path),
        "gcode": str(gcode_path),
        "manifest": str(manifest_path),
        "operations": op_summary,
        "estimated_roles": list(estimated),
        "verification": verification,
        "gcode_size_bytes": gcode_result.get("size_bytes", os.path.getsize(gcode_path)),
    }
    return summary


def _build_ladder_svg(powers: list[int], length: float, pitch: float) -> str:
    """Build a scrap-sized test-pattern SVG with one red horizontal line per power."""
    width = length + 20.0
    height = 10.0 + (len(powers) - 1) * pitch + 10.0
    ns = "http://www.w3.org/2000/svg"
    root = ET.Element(
        "svg",
        {
            "xmlns": ns,
            "width": f"{width}mm",
            "height": f"{height}mm",
            "viewBox": f"0 0 {width} {height}",
        },
    )
    for i in range(len(powers)):
        y = 10.0 + i * pitch
        line = ET.Element(
            "line",
            {
                "x1": "10",
                "y1": str(y),
                "x2": str(10.0 + length),
                "y2": str(y),
                "stroke": "#ff0000",
                "stroke-width": "0.2",
            },
        )
        root.append(line)
    return ET.tostring(root, encoding="unicode")


def _validate_ladder_params(
    powers: list[int],
    speed: float,
    bed_width: float,
    bed_height: float,
    *,
    length: float,
    pitch: float,
    passes: int,
) -> None:
    if not powers:
        raise JobPrepError("--powers must contain at least one power value")
    for p in powers:
        if not isinstance(p, int) or p < 1 or p > 1000:
            raise JobPrepError(f"power {p!r} is outside the valid range 1..1000")
    if speed <= 0:
        raise JobPrepError(f"speed {speed!r} must be greater than zero")
    # Finding 8: ladder geometry must be positive before any file is written or
    # the kernel is booted, so a bad length/pitch/passes cannot reach the laser.
    if length <= 0:
        raise JobPrepError(f"length {length!r} must be greater than zero")
    if pitch <= 0:
        raise JobPrepError(f"pitch {pitch!r} must be greater than zero")
    if passes < 1:
        raise JobPrepError(f"passes {passes!r} must be an integer >= 1")
    # Geometry starts at 10,10; lines extend length+10 right and 10+(n-1)*pitch down.


def prepare_ladder(
    out_dir: str,
    *,
    machine: str,
    role: str,
    powers: list[int],
    speed: float,
    passes: int = 1,
    length: float = 20.0,
    pitch: float = 6.0,
    config_home: Optional[str] = None,
) -> dict[str, Any]:
    """Generate a calibration ladder SVG and G-code for one role.

    The ladder contains one horizontal line per power value, all sharing the
    same feed. Each line gets its own operation so the G-code's S values
    directly correspond to the requested powers.
    """

    bed_width, bed_height = _load_machine_bed(machine)
    _validate_ladder_params(
        powers, speed, bed_width, bed_height, length=length, pitch=pitch, passes=passes
    )

    max_y = 10.0 + (len(powers) - 1) * pitch
    if (10.0 + length) > bed_width or max_y > bed_height:
        raise JobPrepError(
            f"ladder pattern ({10.0 + length:.1f}x{max_y:.1f} mm) exceeds "
            f"machine bed ({bed_width:.1f}x{bed_height:.1f} mm)"
        )

    out_path = Path(out_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)
    stem = f"ladder_{role}"
    svg_path = out_path / f"{stem}.svg"
    gcode_path = out_path / f"{stem}.gcode"

    svg_text = _build_ladder_svg(powers, length, pitch)
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg_text)
        fh.write("\n")

    kind = "cut" if role == "cut" else "engrave"
    backend = Meerk40tBackend(device="grbl")
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        backend.start()
        try:
            backend.run("service device start -i grbl 0")
            _set_bed_and_realize(backend, machine)
            backend.load_file(str(svg_path))
            operations.clear_operations(backend)
            elems = backend.elems()
            if len(elems) < len(powers):
                raise RuntimeError(
                    f"ladder SVG loaded {len(elems)} element(s), expected {len(powers)}"
                )
            op_objects: list[Any] = []
            for i, power in enumerate(powers):
                backend.run(
                    f"{kind} --color #ff0000 --speed {speed} --power {power} --passes {passes}"
                )
                ops = backend.ops()
                op = ops[-1]
                op_objects.append(op)
                op.add_reference(elems[i])
            backend.save_svg(str(svg_path))
            gcode_result = export.export_gcode(
                backend, str(gcode_path), allow_full_power=True
            )
        finally:
            backend.shutdown()

    if gcode_result.get("error"):
        raise RuntimeError(f"G-code export failed: {gcode_result['error']}")
    if not os.path.exists(gcode_path) or os.path.getsize(gcode_path) == 0:
        raise RuntimeError("G-code export produced no output file")

    # Capture each output buffer EXACTLY ONCE (immutable single read).
    gcode_bytes = Path(gcode_path).read_bytes()
    sha_gcode = hashlib.sha256(gcode_bytes).hexdigest()
    svg_bytes = Path(svg_path).read_bytes()
    sha_svg = hashlib.sha256(svg_bytes).hexdigest()

    verification = verify_gcode(
        gcode_bytes.decode("utf-8", "replace"),
        bed_width=bed_width,
        bed_height=bed_height,
        valid_burn_s=set(powers),
        expected_passes=len(powers),
        is_ladder=True,
    )
    # Ladders have no per-element assignment; every line is deliberate.
    verification["unassigned_elements"] = []
    verification["no_unassigned"] = True
    verification["all_passed"] = bool(
        verification["all_passed"] and verification["no_unassigned"]
    )
    # Fail-closed: a ladder whose verification did not pass must NOT emit usable
    # instructions. Raise immediately rather than recording a bad verdict.
    if not verification["all_passed"]:
        raise JobPrepError(
            "ladder verification failed: " + "; ".join(verification["notes"])
        )

    op_summary = []
    for i, power in enumerate(powers):
        op_summary.append(
            {
                "kind": kind,
                "color": "#ff0000",
                "passes": passes,
                "power": power,
                "speed": speed,
                "elements": 1,
            }
        )

    manifest_path = _write_manifest(
        out_path,
        stem,
        machine=machine,
        material="",
        files={
            "input_svg": {
                "path": os.path.relpath(str(svg_path), out_path),
                "sha256": sha_svg,
            },
            "job_svg": {
                "path": os.path.relpath(str(svg_path), out_path),
                "sha256": sha_svg,
            },
            "gcode": {
                "path": os.path.relpath(str(gcode_path), out_path),
                "sha256": sha_gcode,
            },
        },
        operations=op_summary,
        estimated_roles=[],
        settings_fingerprint=None,
        verification=verification,
        kind="ladder",
        role=role,
        powers=powers,
    )

    summary = {
        "input": str(svg_path),
        "job_svg": str(svg_path),
        "gcode": str(gcode_path),
        "manifest": str(manifest_path),
        "operations": op_summary,
        "estimated_roles": [],
        "verification": verification,
        "gcode_size_bytes": gcode_result.get("size_bytes", os.path.getsize(gcode_path)),
    }
    return summary
