#!/usr/bin/env python3
"""Prepare a MeerK40t laser job from an SVG that follows the kraft-house layer contract.

The SVG is expected to contain three top-level groups keyed by stroke colour:
  - #FF0000 red  -> cut   (3 passes, S=950, 6 mm/s)
  - #0000FF blue -> score (1 pass,  S=280, 20 mm/s, engrave)
  - #000000 black -> etch (1 pass,  S=380, 40 mm/s, engrave)

These values are calibration starting points for 350gsm kraft card on a 5.5W
diode (Sculpfun S9 class). They are NOT guaranteed to cut this batch of card;
always test on a scrap piece first.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

from cli_anything.meerk40t.core import export, operations
from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend


# Bed size for the example machine (Sculpfun S9). The design fits inside this.
BED_WIDTH_MM = 410.0
BED_HEIGHT_MM = 400.0

# Laser settings: calibration starting points, not guaranteed.
OP_CUT = {"kind": "cut", "color": "#ff0000", "passes": 3, "power": 950, "speed": 6.0}
OP_SCORE = {"kind": "engrave", "color": "#0000ff", "passes": 1, "power": 280, "speed": 20.0}
OP_ETCH = {"kind": "engrave", "color": "#000000", "passes": 1, "power": 380, "speed": 40.0}
# Etch first, score second, cut LAST: through-cuts release parts from the
# sheet, and released parts can shift under later passes.
OPS = [OP_ETCH, OP_SCORE, OP_CUT]

VALID_BURN_S = {950, 280, 380}


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def _build_ops(backend: Meerk40tBackend) -> list[Any]:
    """Create the three colour-keyed operations and return them."""
    operations.clear_operations(backend)
    for op in OPS:
        backend.run(
            f"{op['kind']} --color {op['color']} "
            f"--speed {op['speed']} --power {op['power']} --passes {op['passes']}"
        )
    return backend.ops()


def _assign_elements_by_color(backend: Meerk40tBackend) -> dict[str, int]:
    """Assign every loaded element to the operation matching its stroke colour."""
    ops = backend.ops()
    elems = backend.elems()
    color_map = {op["color"]: ops[i] for i, op in enumerate(OPS)}
    counts: dict[str, int] = {}
    for e in elems:
        stroke = str(getattr(e, "stroke", "") or "").lower()
        op = color_map.get(stroke)
        if op is None:
            continue
        op.add_reference(e)
        counts[stroke] = counts.get(stroke, 0) + 1
    return counts


def _set_bed_and_realize(backend: Meerk40tBackend) -> None:
    """Set the active GRBL device's bed size and realise it."""
    dev = backend.device()
    if dev is None:
        raise RuntimeError("No GRBL device is active")
    dev.bedwidth = f"{BED_WIDTH_MM}mm"
    dev.bedheight = f"{BED_HEIGHT_MM}mm"
    dev.realize()


def _parse_gcode(gcode_text: str) -> dict[str, Any]:
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
        end_ok = (
            non_empty[-2].upper() == "G1 S0"
            and non_empty[-1].upper() == "M5"
        )

    x_range = [min(x_vals), max(x_vals)] if x_vals else [0.0, 0.0]
    y_range = [min(y_vals), max(y_vals)] if y_vals else [0.0, 0.0]
    in_bounds = (
        x_range[0] >= 0.0
        and x_range[1] <= BED_WIDTH_MM
        and y_range[0] >= 0.0
        and y_range[1] <= BED_HEIGHT_MM
    )

    burn_s_ok = all(s in VALID_BURN_S or s == 0 for s in g1_s_values) and bool(
        VALID_BURN_S.intersection(s_values)
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


def _verify_gcode_file(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return _parse_gcode(text)


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


def prepare_job(input_svg: str, out_dir: str) -> dict[str, Any]:
    """Load input_svg, build ops, export job SVG and G-code, and verify."""
    in_path = Path(input_svg).resolve()
    out_path = Path(out_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)
    stem = in_path.stem
    job_svg_path = out_path / f"{stem}_job.svg"
    gcode_path = out_path / f"{stem}.gcode"

    if not in_path.exists():
        raise FileNotFoundError(f"Input SVG not found: {in_path}")

    backend = Meerk40tBackend(device="grbl")
    # Capture the console channel print so it does not pollute stdout.
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        backend.start()
        try:
            # Ensure the GRBL device is active before configuring bed/ops.
            backend.run("service device start -i grbl 0")
            _set_bed_and_realize(backend)
            backend.load_file(str(in_path))
            _build_ops(backend)
            element_counts = _assign_elements_by_color(backend)
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

    verification = _verify_gcode_file(str(gcode_path))
    checks = [
        verification["header_ok"],
        verification["travel_s0_ok"],
        verification["burn_s_ok"],
        verification["end_ok"],
        verification["in_bounds"],
    ]
    verification["all_passed"] = all(checks)

    op_summary = []
    for i, op in enumerate(OPS):
        op_summary.append(
            {
                "kind": op["kind"],
                "color": op["color"],
                "passes": op["passes"],
                "power": op["power"],
                "speed": op["speed"],
                "elements": element_counts.get(op["color"], 0),
            }
        )

    summary = {
        "input": str(in_path),
        "job_svg": str(job_svg_path),
        "gcode": str(gcode_path),
        "operations": op_summary,
        "verification": verification,
        "gcode_size_bytes": gcode_result.get("size_bytes", os.path.getsize(gcode_path)),
    }
    return summary


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a MeerK40t laser job SVG and G-code from a colour-keyed SVG."
    )
    parser.add_argument("input_svg", help="Input SVG following the layer colour contract")
    parser.add_argument("--out-dir", default=".", help="Directory for output files")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = parser.parse_args(argv)

    try:
        summary = prepare_job(args.input_svg, args.out_dir)
    except Exception as exc:
        _stderr(f"Job preparation failed: {exc}")
        return 1

    if not summary["verification"]["all_passed"]:
        _stderr("G-code verification failed.")
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            _stderr(_human_summary(summary))
        return 1

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(_human_summary(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
