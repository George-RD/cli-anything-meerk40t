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
    resolve_settings as resolve_material_settings,
)
from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend
from cli_anything.meerk40t.utils.profiles import load_profile

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
    material = load_material(material_name, config_home=config_home)
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


def _assign_elements_by_color(backend: Meerk40tBackend) -> tuple[dict[str, int], list[str]]:
    """Assign every loaded element to the operation matching its stroke colour.

    Returns (counts by colour, descriptions of unassigned drawable elements).
    Elements with no stroke at all (containers, structural nodes) are skipped;
    a drawable element with an unrecognised stroke colour is reported so the
    job cannot silently drop geometry.
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
        raw = getattr(e, "stroke", None)
        stroke = str(raw or "").lower()
        op = color_to_op.get(stroke)
        if op is None:
            if raw is not None and stroke not in ("", "none"):
                unassigned.append(f"{getattr(e, 'type', '?')} stroke={stroke}")
            continue
        op.add_reference(e)
        counts[stroke] = counts.get(stroke, 0) + 1
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


def _parse_gcode(
    gcode_text: str, *, bed_width: float, bed_height: float, valid_burn_s: set[int]
) -> dict[str, Any]:
    """Parse G-code for bounds, S values, and structural checks."""
    lines = [line.strip() for line in gcode_text.splitlines()]
    non_empty = [line for line in lines if line]

    x_vals: list[float] = []
    y_vals: list[float] = []
    s_values: set[int] = set()
    g1_s_values: set[int] = set()
    travel_s0_ok = True

    motion_re = re.compile(r"^[GGMm](0|1|00|01)\b")

    for line in non_empty:
        is_motion = bool(motion_re.match(line))
        if is_motion:
            xm = re.search(r"X(-?[\d.]+)", line)
            ym = re.search(r"Y(-?[\d.]+)", line)
            if xm:
                x_vals.append(float(xm.group(1)))
            if ym:
                y_vals.append(float(ym.group(1)))

        sm = re.search(r"S(-?[\d.]+)", line)
        if sm:
            try:
                s = int(float(sm.group(1)))
                s_values.add(s)
                if line.upper().startswith("G1") or line.upper().startswith("G01"):
                    g1_s_values.add(s)
            except ValueError:
                pass

        if line.upper().startswith("G0") or line.upper().startswith("G00"):
            if "S0" not in line.upper():
                travel_s0_ok = False

    header = " ".join(non_empty[:20]).upper()
    header_ok = "G21" in header and "G90" in header and "M4" in header

    end_ok = False
    if len(non_empty) >= 2:
        end_ok = non_empty[-2].upper() == "G1 S0" and non_empty[-1].upper() == "M5"

    x_range = [min(x_vals), max(x_vals)] if x_vals else [0.0, 0.0]
    y_range = [min(y_vals), max(y_vals)] if y_vals else [0.0, 0.0]
    in_bounds = (
        x_range[0] >= 0.0
        and x_range[1] <= bed_width
        and y_range[0] >= 0.0
        and y_range[1] <= bed_height
    )

    burn_s_ok = all(s in valid_burn_s or s == 0 for s in g1_s_values) and bool(
        valid_burn_s.intersection(s_values)
    )

    return {
        "header_ok": header_ok,
        "travel_s0_ok": travel_s0_ok,
        "burn_s_ok": burn_s_ok,
        "s_values": sorted(s_values),
        "g1_s_values": sorted(g1_s_values),
        "end_ok": end_ok,
        "x_range": x_range,
        "y_range": y_range,
        "in_bounds": in_bounds,
    }


def _verify_gcode_file(
    path: str, *, bed_width: float, bed_height: float, valid_burn_s: set[int]
) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return _parse_gcode(
        text, bed_width=bed_width, bed_height=bed_height, valid_burn_s=valid_burn_s
    )


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
    files: dict[str, str],
    operations: list[dict],
    estimated_roles: list[str],
    settings_fingerprint: str | None,
    verification: dict,
    kind: str = "job",
    role: str | None = None,
    powers: list[int] | None = None,
) -> Path:
    """Write a clia-job-manifest-v1 JSON file and return its path."""
    manifest_path = out_dir / f"{stem}_manifest.json"
    manifest: dict[str, Any] = {
        "schema": "clia-job-manifest-v1",
        "kind": kind,
        "created": datetime.now(timezone.utc).isoformat(),
        "machine": machine,
        "material": material,
        "files": {
            name: {"path": str(Path(path).resolve()), "sha256": _sha256_file(path)}
            for name, path in files.items()
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

    valid_powers = {s["power"] for s in settings.values()}
    verification = _verify_gcode_file(
        str(gcode_path),
        bed_width=bed_width,
        bed_height=bed_height,
        valid_burn_s=valid_powers,
    )
    verification["unassigned_elements"] = unassigned
    verification["no_unassigned"] = not unassigned
    checks = [
        verification["header_ok"],
        verification["travel_s0_ok"],
        verification["burn_s_ok"],
        verification["end_ok"],
        verification["in_bounds"],
        verification["no_unassigned"],
    ]
    verification["all_passed"] = all(checks)

    op_summary = []
    for role in BURN_ORDER:
        if role not in settings:
            continue
        s = settings[role]
        color = _color_for_role(role, color_map)
        op_summary.append(
            {
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
            "input_svg": str(in_path),
            "job_svg": str(job_svg_path),
            "gcode": str(gcode_path),
        },
        operations=op_summary,
        estimated_roles=estimated,
        settings_fingerprint=_settings_fingerprint(settings),
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
    powers: list[int], speed: float, bed_width: float, bed_height: float
) -> None:
    if not powers:
        raise JobPrepError("--powers must contain at least one power value")
    for p in powers:
        if not isinstance(p, int) or p < 1 or p > 1000:
            raise JobPrepError(f"power {p!r} is outside the valid range 1..1000")
    if speed <= 0:
        raise JobPrepError(f"speed {speed!r} must be greater than zero")
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
    _validate_ladder_params(powers, speed, bed_width, bed_height)

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

    valid_burn_s = set(powers)
    verification = _verify_gcode_file(
        str(gcode_path),
        bed_width=bed_width,
        bed_height=bed_height,
        valid_burn_s=valid_burn_s,
    )
    verification["unassigned_elements"] = []
    verification["no_unassigned"] = True
    checks = [
        verification["header_ok"],
        verification["travel_s0_ok"],
        verification["burn_s_ok"],
        verification["end_ok"],
        verification["in_bounds"],
        verification["no_unassigned"],
    ]
    verification["all_passed"] = all(checks)

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
            "input_svg": str(svg_path),
            "job_svg": str(svg_path),
            "gcode": str(gcode_path),
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
