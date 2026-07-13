"""Modal G-code state-machine verifier.

Every safety fact is derived FRESH from the G-code text. The verifier never
trusts a stored verdict: the same text + parameters always yields the same
structured result, and malformed G-code returns structured booleans rather
than raising.
"""
from __future__ import annotations

import math
import re

_G_NUM = re.compile(r"G(-?\d+)")
_M_NUM = re.compile(r"M(-?\d+)")
_COORD = re.compile(r"([XYZIJF])\s*(-?[\d.]+)", re.IGNORECASE)


def _num(token: str | None) -> float:
    """Parse a numeric token; non-parseable -> NaN (non-finite)."""
    if token is None:
        return float("nan")
    try:
        return float(token)
    except (ValueError, TypeError):
        return float("nan")


def verify_gcode(
    text: str,
    *,
    bed_width: float,
    bed_height: float,
    valid_burn_s: set[int],
    expected_passes: int | None = None,
    is_ladder: bool = False,
) -> dict:
    """Verify G-code as a modal state machine and return a structured dict.

    Never raises on malformed G-code; returns structured booleans instead.
    """
    # --- modal state ---
    units_metric = True
    coord_absolute = True
    g21_seen = False
    g90_seen = False
    laser_mode_set = False
    laser_on = False
    effective_s = 0.0
    effective_f = 0.0
    coord_rel = False  # True once G91 (relative) mode is active

    cur_x = 0.0
    cur_y = 0.0

    first_motion = False
    header_ok = False

    travel_s0_ok = True
    burn_s_ok = True
    feed_ok = True
    arcs_ok = True
    ordering_ok = True

    xmin = xmax = ymin = ymax = None  # bounds of every visited position

    s_values: set[int] = set()
    g1_s_values: set[int] = set()
    burn_s_seen_order: list[int] = []  # effective S at each powered G1/G2/G3
    last_motion_s = 0.0

    notes: list[str] = []

    def expand():
        nonlocal xmin, xmax, ymin, ymax
        if xmin is None:
            xmin = xmax = cur_x
            ymin = ymax = cur_y
        else:
            xmin = min(xmin, cur_x)
            xmax = max(xmax, cur_x)
            ymin = min(ymin, cur_y)
            ymax = max(ymax, cur_y)

    for raw in text.splitlines():
        # drop (...) comments, then everything after ';'
        line = re.sub(r"\([^)]*\)", "", raw)
        semi = line.find(";")
        if semi != -1:
            line = line[:semi]
        line = line.strip()
        if not line:
            continue

        up = line.upper()
        g_nums = [int(x) for x in _G_NUM.findall(up)]
        m_nums = [int(x) for x in _M_NUM.findall(up)]

        # coordinate / parameter tokens
        def val(letter: str) -> float:
            m = re.search(letter + r"(-?[\d.]+)", up)
            return _num(m.group(1)) if m else float("nan")

        xv = val("X")
        yv = val("Y")
        iv = val("I")
        jv = val("J")
        fv = val("F")

        sm = re.search(r"S(-?[\d.]+)", up)
        s_present = sm is not None
        s_int = int(_num(sm.group(1))) if s_present else 0

        # --- units & coordinate mode ---
        if 20 in g_nums:
            units_metric = False
        if 21 in g_nums:
            g21_seen = True
        if 90 in g_nums:
            g90_seen = True
            coord_rel = False
        if 91 in g_nums:
            coord_rel = True
            # Any G91 means the program is not purely absolute, so the
            # absolute-coordinate safety flag must be cleared.
            coord_absolute = False

        # --- laser state ---
        if 3 in m_nums or 4 in m_nums:
            laser_mode_set = True
            laser_on = True
        if 5 in m_nums:
            laser_on = False

        # --- S / F modal updates ---
        if s_present:
            s_values.add(s_int)
            effective_s = float(s_int)
        if 1 in g_nums and s_present:
            g1_s_values.add(s_int)
        if math.isfinite(fv):
            effective_f = fv

        # --- motion ---
        motion = next((g for g in (0, 1, 2, 3) if g in g_nums), None)
        if motion is None:
            continue

        if not first_motion:
            first_motion = True
            header_ok = g21_seen and g90_seen and laser_mode_set

        last_motion_s = effective_s

        if motion == 0:
            # travel move: laser must be off or S0
            if laser_on and effective_s != 0:
                travel_s0_ok = False
            if math.isfinite(xv):
                cur_x = xv if not coord_rel else cur_x + xv
            if math.isfinite(yv):
                cur_y = yv if not coord_rel else cur_y + yv
            expand()
            continue

        # G1 / G2 / G3 = powered cutting moves
        powered = laser_on
        if powered:
            eff_s = int(effective_s)
            if not (eff_s == 0 or eff_s in valid_burn_s):
                burn_s_ok = False
            if not (math.isfinite(effective_f) and effective_f > 0):
                feed_ok = False
            burn_s_seen_order.append(eff_s)

        if motion in (2, 3):
            # Arc: the ENDPOINT is the X/Y word (absolute, or a relative delta
            # if G91 is active). I/J are CENTER offsets from the current point
            # and are only validated for finiteness; they do not move the
            # endpoint. The endpoint defaults to the current position when no
            # X/Y word is present on the arc line.
            if not (math.isfinite(iv) and math.isfinite(jv)):
                arcs_ok = False
            if math.isfinite(xv):
                end_x = xv if not coord_rel else cur_x + xv
            else:
                end_x = cur_x
            if math.isfinite(yv):
                end_y = yv if not coord_rel else cur_y + yv
            else:
                end_y = cur_y
            if not (0.0 <= end_x <= bed_width and 0.0 <= end_y <= bed_height):
                arcs_ok = False
            cur_x, cur_y = end_x, end_y
        else:
            # G1
            if math.isfinite(xv):
                cur_x = xv if not coord_rel else cur_x + xv
            if math.isfinite(yv):
                cur_y = yv if not coord_rel else cur_y + yv
        expand()

    # --- derive final verdicts ---
    laser_off_final = not laser_on
    end_ok = laser_off_final and (last_motion_s == 0)
    beam_off = end_ok

    in_bounds = (xmin is None) or (
        xmin >= 0.0 and xmax <= bed_width and ymin >= 0.0 and ymax <= bed_height
    )

    burn_s_seen = sorted({s for s in burn_s_seen_order})
    valid_burn_present = any(s in valid_burn_s and s != 0 for s in burn_s_seen)
    burn_s_ok = burn_s_ok and valid_burn_present

    # ordering: a "cut" power (max valid burn S) must never precede an
    # etch/engrave power (lower valid burn S).
    if valid_burn_s:
        cut_s = max(valid_burn_s)
        saw_cut = False
        for s in burn_s_seen_order:
            if s == cut_s:
                saw_cut = True
            elif s in valid_burn_s:
                if saw_cut:
                    ordering_ok = False
                    break

    if expected_passes is None:
        pass_coverage_ok = True
    else:
        burn_layers = len([s for s in burn_s_seen if s in valid_burn_s and s != 0])
        pass_coverage_ok = burn_layers >= expected_passes

    if is_ladder:
        ladder_coverage = set(valid_burn_s).issubset(set(burn_s_seen))
    else:
        ladder_coverage = True

    core = [
        header_ok,
        units_metric,
        coord_absolute,
        travel_s0_ok,
        burn_s_ok,
        feed_ok,
        end_ok,
        arcs_ok,
        in_bounds,
        ordering_ok,
        pass_coverage_ok,
    ]
    all_passed = all(core) and (ladder_coverage if is_ladder else True)

    # --- notes ---
    if not header_ok:
        notes.append("Missing G21/G90/laser-mode (M3/M4) before first motion")
    if not units_metric:
        notes.append("Imperial units (G20) detected")
    if not coord_absolute:
        notes.append("Relative coordinates (G91) used after header")
    if not travel_s0_ok:
        notes.append("Travel move (G0) with laser powered and S != 0")
    if not burn_s_ok:
        notes.append("Powered move with invalid/unlisted burn S, or no valid burn")
    if not feed_ok:
        notes.append("Powered move without a finite feed rate F > 0")
    if not end_ok:
        notes.append("Job did not end with beam off (M5 and final S == 0)")
    if not arcs_ok:
        notes.append("Arc (G2/G3) with non-finite I/J or out-of-bounds endpoint")
    if not in_bounds:
        notes.append("Motion position outside the bed bounds")
    if not ordering_ok:
        notes.append("Cut power appeared before etch/engrave power")
    if not pass_coverage_ok:
        notes.append("Fewer powered burn layers than expected_passes")
    if is_ladder and not ladder_coverage:
        notes.append("Ladder missing a required power level")

    if xmin is None:
        x_range: list[float] = [0.0, 0.0]
        y_range: list[float] = [0.0, 0.0]
    else:
        x_range = [xmin, xmax]
        y_range = [ymin, ymax]

    return {
        "header_ok": header_ok,
        "units_metric": units_metric,
        "coord_absolute": coord_absolute,
        "travel_s0_ok": travel_s0_ok,
        "burn_s_ok": burn_s_ok,
        "feed_ok": feed_ok,
        "end_ok": end_ok,
        "arcs_ok": arcs_ok,
        "in_bounds": in_bounds,
        "ordering_ok": ordering_ok,
        "pass_coverage_ok": pass_coverage_ok,
        "ladder_coverage": ladder_coverage,
        "beam_off": beam_off,
        "no_unassigned": True,
        "all_passed": all_passed,
        "s_values": sorted(s_values),
        "g1_s_values": sorted(g1_s_values),
        "x_range": x_range,
        "y_range": y_range,
        "burn_s_seen": burn_s_seen,
        "notes": notes,
    }
