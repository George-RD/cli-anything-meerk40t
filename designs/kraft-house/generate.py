#!/usr/bin/env python3
"""Generate the single-sheet A3 kraft house net as a layered SVG.

Spec: local://house-net-spec.md (v1). This script is stdlib-only. It writes
house_a3.svg in the same directory and runs self-validation.
"""

import math
import os
import sys
import xml.etree.ElementTree as ET
from xml.dom import minidom

# ---------------------------------------------------------------------------
# Geometric deviations from the authoritative spec (all kept locally, intent
# preserved):
# - Window frame etch border is 1.5mm instead of 2.5mm. The two front windows
#   are only 4mm apart, so 2.5mm frames would overlap; the smaller offset is
#   applied to all window frames (rectangular and circular) for consistency.
# - Chimney tongue/slot flat-net positions: see note in the chimney section.
# ---------------------------------------------------------------------------

# --- sheet / working window ------------------------------------------------
DOC_W, DOC_H = 420.0, 297.0
WORK_X0, WORK_X1 = 10.0, 410.0
WORK_Y0, WORK_Y1 = 8.5, 288.5
EPS = 1e-6

# --- wall strip ------------------------------------------------------------
WALL_LEFT = 20.0
WALL_TOP = 60.0
WALL_BOTTOM = 140.0

FRONT_X0 = 20.0
FRONT_X1 = 120.0
SIDE_A_X0 = 120.0
SIDE_A_X1 = 200.0
BACK_X0 = 200.0
BACK_X1 = 300.0
SIDE_B_X0 = 300.0
SIDE_B_X1 = 380.0
GLUE_TAB_X0 = 380.0
GLUE_TAB_X1 = 392.0

GABLE_PEAK_Y = 15.0
GABLE_A_PEAK = (160.0, GABLE_PEAK_Y)
GABLE_B_PEAK = (340.0, GABLE_PEAK_Y)

TAB_DEPTH = 10.0
TAB_CHAMFER = 4.0

# --- door / windows / gable circles ----------------------------------------
DOOR_W = 32.0
DOOR_H = 55.0
# Door sits left of centre so both front windows fit to its right with
# full etch clearance (a flanking layout would overlap the door cut).
DOOR_CX = 48.0
DOOR_X0 = DOOR_CX - DOOR_W / 2.0
DOOR_X1 = DOOR_CX + DOOR_W / 2.0
DOOR_Y0 = WALL_BOTTOM - DOOR_H
DOOR_Y1 = WALL_BOTTOM

WIN_W = 20.0
WIN_H = 26.0
WIN_Y0 = 72.0
WIN_Y1 = WIN_Y0 + WIN_H

# Front windows both right of the door, 4 mm apart, 4 mm from the wall edge
FRONT_WIN_LX0 = 72.0
FRONT_WIN_LX1 = FRONT_WIN_LX0 + WIN_W
FRONT_WIN_RX0 = 96.0
FRONT_WIN_RX1 = FRONT_WIN_RX0 + WIN_W

SIDE_A_WIN_X0 = 150.0
SIDE_A_WIN_X1 = SIDE_A_WIN_X0 + WIN_W

BACK_WIN_LX0 = 220.0
BACK_WIN_LX1 = BACK_WIN_LX0 + WIN_W
BACK_WIN_RX0 = 260.0
BACK_WIN_RX1 = BACK_WIN_RX0 + WIN_W

SIDE_B_WIN_X0 = 330.0
SIDE_B_WIN_X1 = SIDE_B_WIN_X0 + WIN_W

GABLE_WIN_CX_A = 160.0
GABLE_WIN_CX_B = 340.0
GABLE_WIN_CY = 42.0
GABLE_WIN_R = 7.0

# --- roof ------------------------------------------------------------------
ROOF_X0 = 20.0
ROOF_X1 = 130.0
ROOF_Y0 = 160.0
ROOF_Y1 = 284.0
ROOF_RIDGE_Y = 222.0
# Chimney slots: TWO slots oriented along the slope (flat y direction), one
# under each angled (perpendicular-to-ridge) chimney face. In 3D the tongues
# sit on those two faces, so a single ridge-parallel slot cannot take both.
# Chimney footprint on the flat panel: 14 mm along the ridge (x 68..82),
# 14/cos(pitch) = 21.07 mm along the slope, seat centre 18.5 mm from ridge.
ROOF_SLOT_YC = 203.5          # slope-centre of the chimney seat (flat y)
ROOF_SLOT_LEN = 8.0           # along slope; tongue projects ~6 mm
ROOF_SLOT_W = 3.0             # across; card is 0.35 mm thick
ROOF_SLOTS = [
    (66.5, ROOF_SLOT_YC - ROOF_SLOT_LEN / 2.0,
     69.5, ROOF_SLOT_YC + ROOF_SLOT_LEN / 2.0),
    (80.5, ROOF_SLOT_YC - ROOF_SLOT_LEN / 2.0,
     83.5, ROOF_SLOT_YC + ROOF_SLOT_LEN / 2.0),
]

# --- chimney ---------------------------------------------------------------
CHIM_X0 = 150.0
CHIM_FACE_W = 14.0
CHIM_TOP_Y = 170.0
CHIM_HEIGHT = 30.0
CHIM_TAB_W = 6.0
CHIM_X1 = CHIM_X0 + 4 * CHIM_FACE_W + CHIM_TAB_W  # 212

PITCH_ANGLE = math.atan2(45.0, 40.0)
PITCH_TAN = math.tan(PITCH_ANGLE)
CHIM_SLOPE_OFFSET = CHIM_FACE_W * PITCH_TAN       # 15.75
CHIM_NOTCH_DEPTH = (CHIM_FACE_W / 2.0) * PITCH_TAN  # 7.875
TONGUE_DEPTH = 3.0
TONGUE_WIDTH = 4.0

ETCH_MARGIN = 3.0

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

# --- helpers ---------------------------------------------------------------

def fmt(n):
    """Compact number formatter for SVG attributes."""
    s = f"{n:.3f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s == "-0":
        s = "0"
    return s


def points_to_path(points):
    """Convert a list of (x, y) points to an SVG path d attribute."""
    if not points:
        return ""
    d = f"M {fmt(points[0][0])} {fmt(points[0][1])}"
    for x, y in points[1:]:
        d += f" L {fmt(x)} {fmt(y)}"
    d += " Z"
    return d


def add_path(parent, d, stroke, sw="0.2", eid=None):
    p = ET.SubElement(parent, "path")
    p.set("d", d)
    p.set("stroke", stroke)
    p.set("stroke-width", sw)
    p.set("fill", "none")
    if eid:
        p.set("id", eid)
    return p


def add_line(parent, x1, y1, x2, y2, stroke, sw="0.2", eid=None):
    line = ET.SubElement(parent, "line")
    line.set("x1", fmt(x1))
    line.set("y1", fmt(y1))
    line.set("x2", fmt(x2))
    line.set("y2", fmt(y2))
    line.set("stroke", stroke)
    line.set("stroke-width", sw)
    line.set("fill", "none")
    if eid:
        line.set("id", eid)
    return line


# --- element collectors ----------------------------------------------------
etch_elements = []   # (name, path_d)
score_elements = []  # (name, x1, y1, x2, y2)
cut_elements = []    # (name, path_d)
wall_perim_points = [
    (WALL_LEFT, WALL_TOP),                       # front wall top-left
    (FRONT_X1, WALL_TOP),                        # front wall top-right
    GABLE_A_PEAK,                                # gable A peak
    (SIDE_A_X1, WALL_TOP),                       # side A top-right
    (BACK_X1, WALL_TOP),                         # back wall top-left
    GABLE_B_PEAK,                                # gable B peak
    (SIDE_B_X1, WALL_TOP),                       # side B top-right
    (GLUE_TAB_X0 + TAB_CHAMFER, WALL_TOP),       # glue tab top-left
    (GLUE_TAB_X1, WALL_TOP + TAB_CHAMFER),       # glue tab top chamfer
    (GLUE_TAB_X1, WALL_BOTTOM - TAB_CHAMFER),    # glue tab right edge
    (GLUE_TAB_X0 + TAB_CHAMFER, WALL_BOTTOM),    # glue tab bottom chamfer
    (GLUE_TAB_X0, WALL_BOTTOM),                  # glue tab bottom-left
    (SIDE_B_X0, WALL_BOTTOM),                    # side B bottom (no tab)
    (SIDE_B_X0 - TAB_CHAMFER, WALL_BOTTOM + TAB_DEPTH),  # back tab right chamfer
    (BACK_X0 + TAB_CHAMFER, WALL_BOTTOM + TAB_DEPTH),    # back tab bottom
    (BACK_X0, WALL_BOTTOM),                      # back tab left chamfer
    (SIDE_A_X1 - TAB_CHAMFER, WALL_BOTTOM + TAB_DEPTH),  # side A tab right chamfer
    (SIDE_A_X0 + TAB_CHAMFER, WALL_BOTTOM + TAB_DEPTH),  # side A tab bottom
    (SIDE_A_X0, WALL_BOTTOM),                      # side A tab left chamfer
    (FRONT_X1 - TAB_CHAMFER, WALL_BOTTOM + TAB_DEPTH), # front tab right chamfer
    (FRONT_X0 + TAB_CHAMFER, WALL_BOTTOM + TAB_DEPTH),   # front tab bottom
    (FRONT_X0, WALL_BOTTOM),                       # front tab left chamfer
    (WALL_LEFT, WALL_TOP),                          # close
]
cut_elements.append(("wall_perim", points_to_path(wall_perim_points)))

# --- cut apertures ---------------------------------------------------------
# Door: cut on left, right, top; hinge is the scored bottom edge.
cut_elements.append(("door_left", f"M {fmt(DOOR_X0)} {fmt(DOOR_Y0)} L {fmt(DOOR_X0)} {fmt(DOOR_Y1)}"))
cut_elements.append(("door_top", f"M {fmt(DOOR_X0)} {fmt(DOOR_Y0)} L {fmt(DOOR_X1)} {fmt(DOOR_Y0)}"))
cut_elements.append(("door_right", f"M {fmt(DOOR_X1)} {fmt(DOOR_Y0)} L {fmt(DOOR_X1)} {fmt(DOOR_Y1)}"))

windows = [
    ("front_left", FRONT_WIN_LX0, WIN_Y0, WIN_W, WIN_H),
    ("front_right", FRONT_WIN_RX0, WIN_Y0, WIN_W, WIN_H),
    ("side_a", SIDE_A_WIN_X0, WIN_Y0, WIN_W, WIN_H),
    ("back_left", BACK_WIN_LX0, WIN_Y0, WIN_W, WIN_H),
    ("back_right", BACK_WIN_RX0, WIN_Y0, WIN_W, WIN_H),
    ("side_b", SIDE_B_WIN_X0, WIN_Y0, WIN_W, WIN_H),
]
for name, x, y, w, h in windows:
    cut_elements.append((name, f"M {fmt(x)} {fmt(y)} L {fmt(x+w)} {fmt(y)} L {fmt(x+w)} {fmt(y+h)} L {fmt(x)} {fmt(y+h)} Z"))

# Circular gable windows (cut apertures)
cut_elements.append(("gable_a_window", f"M {fmt(GABLE_WIN_CX_A - GABLE_WIN_R)} {fmt(GABLE_WIN_CY)} A {fmt(GABLE_WIN_R)} {fmt(GABLE_WIN_R)} 0 1 1 {fmt(GABLE_WIN_CX_A + GABLE_WIN_R)} {fmt(GABLE_WIN_CY)} A {fmt(GABLE_WIN_R)} {fmt(GABLE_WIN_R)} 0 1 1 {fmt(GABLE_WIN_CX_A - GABLE_WIN_R)} {fmt(GABLE_WIN_CY)} Z"))
cut_elements.append(("gable_b_window", f"M {fmt(GABLE_WIN_CX_B - GABLE_WIN_R)} {fmt(GABLE_WIN_CY)} A {fmt(GABLE_WIN_R)} {fmt(GABLE_WIN_R)} 0 1 1 {fmt(GABLE_WIN_CX_B + GABLE_WIN_R)} {fmt(GABLE_WIN_CY)} A {fmt(GABLE_WIN_R)} {fmt(GABLE_WIN_R)} 0 1 1 {fmt(GABLE_WIN_CX_B - GABLE_WIN_R)} {fmt(GABLE_WIN_CY)} Z"))

# Roof outer perimeter
cut_elements.append(("roof_perim", f"M {fmt(ROOF_X0)} {fmt(ROOF_Y0)} L {fmt(ROOF_X1)} {fmt(ROOF_Y0)} L {fmt(ROOF_X1)} {fmt(ROOF_Y1)} L {fmt(ROOF_X0)} {fmt(ROOF_Y1)} Z"))

# Chimney slots (two, slope-direction)
for i, (sx0, sy0, sx1, sy1) in enumerate(ROOF_SLOTS):
    cut_elements.append((f"chimney_slot_{i}", f"M {fmt(sx0)} {fmt(sy0)} L {fmt(sx1)} {fmt(sy0)} L {fmt(sx1)} {fmt(sy1)} L {fmt(sx0)} {fmt(sy1)} Z"))

# --- chimney net perimeter -------------------------------------------------
# F1 / F3 are the ridge-parallel faces (angled parallelogram bottoms);
# F2 / F4 are the perpendicular faces (V-notched bottoms).
# Top edge is straight at y = CHIM_TOP_Y. Bottom edges follow the roof pitch.
F1_top_left = (CHIM_X0, CHIM_TOP_Y)
F1_top_right = (CHIM_X0 + CHIM_FACE_W, CHIM_TOP_Y)
F1_bottom_left = (CHIM_X0, CHIM_TOP_Y + CHIM_HEIGHT)
F1_bottom_right = (CHIM_X0 + CHIM_FACE_W, CHIM_TOP_Y + CHIM_HEIGHT + CHIM_SLOPE_OFFSET)
F1_tongue_cx = CHIM_X0 + CHIM_FACE_W / 2.0
F1_tongue_top_y = F1_bottom_left[1] + (F1_tongue_cx - CHIM_X0) * PITCH_TAN
F1_tongue_bottom_y = F1_tongue_top_y + TONGUE_DEPTH

F2_bottom_left = F1_bottom_right
F2_bottom_right = (CHIM_X0 + 2 * CHIM_FACE_W, CHIM_TOP_Y + CHIM_HEIGHT + CHIM_SLOPE_OFFSET)
F2_notch_cx = CHIM_X0 + 1.5 * CHIM_FACE_W
F2_notch_y = F2_bottom_left[1] - CHIM_NOTCH_DEPTH

F3_bottom_left = F2_bottom_right
F3_bottom_right = (CHIM_X0 + 3 * CHIM_FACE_W, CHIM_TOP_Y + CHIM_HEIGHT)
F3_tongue_cx = CHIM_X0 + 2.5 * CHIM_FACE_W
F3_tongue_top_y = F3_bottom_left[1] + (F3_tongue_cx - (CHIM_X0 + 2 * CHIM_FACE_W)) * (-PITCH_TAN)
F3_tongue_bottom_y = F3_tongue_top_y + TONGUE_DEPTH

F4_bottom_left = F3_bottom_right
F4_bottom_right = (CHIM_X0 + 4 * CHIM_FACE_W, CHIM_TOP_Y + CHIM_HEIGHT)
F4_notch_cx = CHIM_X0 + 3.5 * CHIM_FACE_W
F4_notch_y = F4_bottom_left[1] - CHIM_NOTCH_DEPTH

glue_x0 = CHIM_X0 + 4 * CHIM_FACE_W
glue_x1 = glue_x0 + CHIM_TAB_W
glue_chamfer = 2.0

chimney_perim_points = [
    F1_top_left,
    F1_top_right,
    (F1_tongue_cx - TONGUE_WIDTH / 2.0, F1_tongue_top_y),
    (F1_tongue_cx - TONGUE_WIDTH / 2.0, F1_tongue_bottom_y),
    (F1_tongue_cx + TONGUE_WIDTH / 2.0, F1_tongue_bottom_y),
    (F1_tongue_cx + TONGUE_WIDTH / 2.0, F1_tongue_top_y),
    F1_bottom_right,
    (F2_notch_cx, F2_notch_y),
    F2_bottom_right,
    (F3_tongue_cx - TONGUE_WIDTH / 2.0, F3_tongue_top_y),
    (F3_tongue_cx - TONGUE_WIDTH / 2.0, F3_tongue_bottom_y),
    (F3_tongue_cx + TONGUE_WIDTH / 2.0, F3_tongue_bottom_y),
    (F3_tongue_cx + TONGUE_WIDTH / 2.0, F3_tongue_top_y),
    F3_bottom_right,
    (F4_notch_cx, F4_notch_y),
    F4_bottom_right,
    (glue_x0 + glue_chamfer, F4_bottom_right[1]),
    (glue_x1, F4_bottom_right[1] - glue_chamfer),
    (glue_x1, CHIM_TOP_Y + glue_chamfer),
    (glue_x0 + glue_chamfer, CHIM_TOP_Y),
    F1_top_left,
]
cut_elements.append(("chimney_perim", points_to_path(chimney_perim_points)))

# Note on the chimney lock: the two tongues sit at the middle of the angled
# faces. Folded, each angled face runs up the slope, so its mid-face tongue
# lands at the seat's slope-centre (flat y = ROOF_SLOT_YC), one tongue per
# slot. Tongue is 4 mm wide in the net, ~6 mm projected along the slope,
# inside the 8 mm slot length.

# --- score lines -----------------------------------------------------------
# Vertical wall fold scores
score_elements.append(("score_x120", 120.0, WALL_TOP, 120.0, WALL_BOTTOM))
score_elements.append(("score_x200", 200.0, WALL_TOP, 200.0, WALL_BOTTOM))
score_elements.append(("score_x300", 300.0, WALL_TOP, 300.0, WALL_BOTTOM))
score_elements.append(("score_x380", 380.0, WALL_TOP, 380.0, WALL_BOTTOM))

# Bottom tab fold scores
score_elements.append(("score_front_tab", FRONT_X0, WALL_BOTTOM, FRONT_X1, WALL_BOTTOM))
score_elements.append(("score_side_a_tab", SIDE_A_X0, WALL_BOTTOM, SIDE_A_X1, WALL_BOTTOM))
score_elements.append(("score_back_tab", BACK_X0, WALL_BOTTOM, BACK_X1, WALL_BOTTOM))

# Door hinge: no separate score. The front tab fold score above already
# runs along the door bottom; a second coincident score would cut through.

# Ridge score
score_elements.append(("score_ridge", ROOF_X0, ROOF_RIDGE_Y, ROOF_X1, ROOF_RIDGE_Y))

# Chimney face fold scores at the face boundaries, plus the glue tab fold.
score_elements.append(("score_chim_164", F1_bottom_right[0], CHIM_TOP_Y, F1_bottom_right[0], F1_bottom_right[1]))
score_elements.append(("score_chim_178", F2_bottom_right[0], CHIM_TOP_Y, F2_bottom_right[0], F2_bottom_right[1]))
score_elements.append(("score_chim_192", F3_bottom_right[0], CHIM_TOP_Y, F3_bottom_right[0], F3_bottom_right[1]))
score_elements.append(("score_chim_tab", glue_x0, CHIM_TOP_Y, glue_x0, F4_bottom_right[1]))
# --- etch details ----------------------------------------------------------

# Window frame offset reduced from 2.5mm to 1.5mm because the two front
# windows (20mm wide, 4mm apart) cannot both carry a 2.5mm border without
# overlapping. The 1.5mm frame is applied uniformly to all window frames.
WIN_FRAME_OFFSET = 1.5

# Rectangular window frames (etched border outside each aperture)
for name, x, y, w, h in windows:
    fx = x - WIN_FRAME_OFFSET
    fy = y - WIN_FRAME_OFFSET
    fw = w + 2.0 * WIN_FRAME_OFFSET
    fh = h + 2.0 * WIN_FRAME_OFFSET
    etch_elements.append((f"winframe_{name}", f"M {fmt(fx)} {fmt(fy)} L {fmt(fx+fw)} {fmt(fy)} L {fmt(fx+fw)} {fmt(fy+fh)} L {fmt(fx)} {fmt(fy+fh)} Z"))

# Circular gable window frames (etched ring outside each aperture)
for cx in (GABLE_WIN_CX_A, GABLE_WIN_CX_B):
    r = GABLE_WIN_R + WIN_FRAME_OFFSET
    etch_elements.append((f"gable_winframe_{cx:.0f}", f"M {fmt(cx - r)} {fmt(GABLE_WIN_CY)} A {fmt(r)} {fmt(r)} 0 1 1 {fmt(cx + r)} {fmt(GABLE_WIN_CY)} A {fmt(r)} {fmt(r)} 0 1 1 {fmt(cx - r)} {fmt(GABLE_WIN_CY)} Z"))

# Door planks and handle
for x in [DOOR_X0 + 6.0, DOOR_X0 + 12.0, DOOR_X0 + 18.0, DOOR_X0 + 24.0]:
    etch_elements.append((f"door_plank_{x:.1f}", f"M {fmt(x)} {fmt(DOOR_Y0)} L {fmt(x)} {fmt(DOOR_Y1)}"))
etch_elements.append(("door_handle", f"M {fmt(DOOR_CX - 1.5)} {fmt(DOOR_Y1 - 20.0)} A 1.5 1.5 0 1 1 {fmt(DOOR_CX + 1.5)} {fmt(DOOR_Y1 - 20.0)} A 1.5 1.5 0 1 1 {fmt(DOOR_CX - 1.5)} {fmt(DOOR_Y1 - 20.0)} Z"))

# Brick courses on wall bodies, avoiding apertures and edges
def add_bricks(wall_left, wall_right, wall_top, wall_bottom, avoid_rects):
    y = wall_top + ETCH_MARGIN
    row = 0
    while y < wall_bottom - ETCH_MARGIN:
        # Horizontal course, clipped around avoid rectangles
        hx0 = wall_left + ETCH_MARGIN
        hx1 = wall_right - ETCH_MARGIN
        segs = [(hx0, hx1)]
        for rx0, ry0, rx1, ry1 in avoid_rects:
            if ry0 <= y <= ry1:
                new_segs = []
                for s0, s1 in segs:
                    if s1 <= rx0 or s0 >= rx1:
                        new_segs.append((s0, s1))
                    else:
                        if s0 < rx0:
                            new_segs.append((s0, rx0))
                        if s1 > rx1:
                            new_segs.append((rx1, s1))
                segs = new_segs
        for s0, s1 in segs:
            if s1 - s0 > 0.1:
                etch_elements.append((f"brick_h_{wall_left:.0f}_{y:.1f}_{s0:.1f}", f"M {fmt(s0)} {fmt(y)} L {fmt(s1)} {fmt(y)}"))
        # Vertical joints in this course
        offset = 0.0 if row % 2 == 0 else 8.0
        jx = wall_left + ETCH_MARGIN + offset
        while jx < wall_right - ETCH_MARGIN:
            skip = False
            for rx0, ry0, rx1, ry1 in avoid_rects:
                if rx0 <= jx <= rx1 and not (y + 7.0 <= ry0 or y >= ry1):
                    skip = True
                    break
            if not skip:
                etch_elements.append((f"brick_v_{wall_left:.0f}_{y:.1f}_{jx:.1f}", f"M {fmt(jx)} {fmt(y)} L {fmt(jx)} {fmt(y + 7.0)}"))
            jx += 16.0
        y += 7.0
        row += 1


def pad(rect, m):
    x0, y0, x1, y1 = rect
    return (x0 - m, y0 - m, x1 + m, y1 + m)


front_avoid = [pad((DOOR_X0, DOOR_Y0, DOOR_X1, DOOR_Y1), ETCH_MARGIN),
               pad((FRONT_WIN_LX0, WIN_Y0, FRONT_WIN_LX1, WIN_Y1), ETCH_MARGIN),
               pad((FRONT_WIN_RX0, WIN_Y0, FRONT_WIN_RX1, WIN_Y1), ETCH_MARGIN)]
add_bricks(FRONT_X0, FRONT_X1, WALL_TOP, WALL_BOTTOM, front_avoid)

side_a_avoid = [pad((SIDE_A_WIN_X0, WIN_Y0, SIDE_A_WIN_X1, WIN_Y1), ETCH_MARGIN)]
add_bricks(SIDE_A_X0, SIDE_A_X1, WALL_TOP, WALL_BOTTOM, side_a_avoid)

back_avoid = [pad((BACK_WIN_LX0, WIN_Y0, BACK_WIN_LX1, WIN_Y1), ETCH_MARGIN),
              pad((BACK_WIN_RX0, WIN_Y0, BACK_WIN_RX1, WIN_Y1), ETCH_MARGIN)]
add_bricks(BACK_X0, BACK_X1, WALL_TOP, WALL_BOTTOM, back_avoid)

side_b_avoid = [pad((SIDE_B_WIN_X0, WIN_Y0, SIDE_B_WIN_X1, WIN_Y1), ETCH_MARGIN)]
add_bricks(SIDE_B_X0, SIDE_B_X1, WALL_TOP, WALL_BOTTOM, side_b_avoid)

# Gable timber frames: raking lines parallel to slopes + split king post
# around the circular window.
for x0, x1, peak in ((SIDE_A_X0, SIDE_A_X1, GABLE_A_PEAK),
                     (SIDE_B_X0, SIDE_B_X1, GABLE_B_PEAK)):
    # Inset triangle: shrink the gable triangle 5 mm toward its incentre,
    # then etch only its two slope edges. Untrimmed parallel offsets would
    # cross the opposite cut slope at the apex.
    a_v = (x0, WALL_TOP)
    b_v = peak
    c_v = (x1, WALL_TOP)
    la = math.hypot(c_v[0] - b_v[0], c_v[1] - b_v[1])  # opposite A
    lb = math.hypot(c_v[0] - a_v[0], c_v[1] - a_v[1])  # opposite B
    lc = math.hypot(b_v[0] - a_v[0], b_v[1] - a_v[1])  # opposite C
    per = la + lb + lc
    icx = (la * a_v[0] + lb * b_v[0] + lc * c_v[0]) / per
    icy = (la * a_v[1] + lb * b_v[1] + lc * c_v[1]) / per
    area = abs((c_v[0] - a_v[0]) * (b_v[1] - a_v[1]) -
               (b_v[0] - a_v[0]) * (c_v[1] - a_v[1])) / 2.0
    inradius = area / (per / 2.0)
    k = 1.0 - 5.0 / inradius
    ia = (icx + k * (a_v[0] - icx), icy + k * (a_v[1] - icy))
    ib = (icx + k * (b_v[0] - icx), icy + k * (b_v[1] - icy))
    ic = (icx + k * (c_v[0] - icx), icy + k * (c_v[1] - icy))
    etch_elements.append((f"gable_r_{x0:.0f}",
                          f"M {fmt(ia[0])} {fmt(ia[1])} L {fmt(ib[0])} {fmt(ib[1])}"))
    etch_elements.append((f"gable_l_{x0:.0f}",
                          f"M {fmt(ib[0])} {fmt(ib[1])} L {fmt(ic[0])} {fmt(ic[1])}"))

    # King post, split around the circular gable window (y = 35..49)
    cx = peak[0]
    etch_elements.append((f"gable_king_top_{x0:.0f}", f"M {fmt(cx)} {fmt(ib[1] + 1.5)} L {fmt(cx)} {fmt(GABLE_WIN_CY - GABLE_WIN_R - 1.0)}"))
    etch_elements.append((f"gable_king_bot_{x0:.0f}", f"M {fmt(cx)} {fmt(GABLE_WIN_CY + GABLE_WIN_R + 1.0)} L {fmt(cx)} {fmt(WALL_TOP)}"))

# Roof shingles: staggered horizontal courses, 3 mm clear of edges, skip slot
y = ROOF_Y0 + ETCH_MARGIN
row = 0
while y < ROOF_Y1 - ETCH_MARGIN:
    slot_band = (ROOF_SLOTS[0][1] - ETCH_MARGIN <= y <= ROOF_SLOTS[0][3] + ETCH_MARGIN)
    if not slot_band:
        etch_elements.append((f"shingle_h_{y:.1f}", f"M {fmt(ROOF_X0 + ETCH_MARGIN)} {fmt(y)} L {fmt(ROOF_X1 - ETCH_MARGIN)} {fmt(y)}"))
    else:
        # Split the course around both slots
        xs = [ROOF_X0 + ETCH_MARGIN]
        for sx0, _sy0, sx1, _sy1 in ROOF_SLOTS:
            xs += [sx0 - ETCH_MARGIN, sx1 + ETCH_MARGIN]
        xs.append(ROOF_X1 - ETCH_MARGIN)
        for k in range(0, len(xs), 2):
            if xs[k + 1] - xs[k] > 2.0:
                etch_elements.append((f"shingle_h_{y:.1f}_{k}", f"M {fmt(xs[k])} {fmt(y)} L {fmt(xs[k + 1])} {fmt(y)}"))
    # Vertical joints in this course
    offset = 0.0 if row % 2 == 0 else 4.0
    x = ROOF_X0 + ETCH_MARGIN + offset
    while x < ROOF_X1 - ETCH_MARGIN:
        yb = y + 8.0
        in_slot = any(
            sx0 - ETCH_MARGIN <= x <= sx1 + ETCH_MARGIN and
            not (yb <= sy0 - ETCH_MARGIN or y >= sy1 + ETCH_MARGIN)
            for sx0, sy0, sx1, sy1 in ROOF_SLOTS)
        if yb <= ROOF_Y1 - ETCH_MARGIN and not in_slot:
            etch_elements.append((f"shingle_v_{y:.1f}_{x:.1f}", f"M {fmt(x)} {fmt(y)} L {fmt(x)} {fmt(yb)}"))
        x += 8.0
    y += 8.0
    row += 1

# Roof alignment ticks, 5 mm in from each panel end
for x in (ROOF_X0 + 5.0, ROOF_X1 - 5.0):
    etch_elements.append((f"roof_tick_top_{x:.1f}", f"M {fmt(x)} {fmt(ROOF_RIDGE_Y - 2.0)} L {fmt(x)} {fmt(ROOF_RIDGE_Y + 2.0)}"))
    etch_elements.append((f"roof_tick_bottom_{x:.1f}", f"M {fmt(x)} {fmt(ROOF_Y1 - 5.0)} L {fmt(x)} {fmt(ROOF_Y1)}"))

# Chimney brick etch (safe central region, clear of tongues and V-notches)
for y in (177.0, 184.0, 191.0, 198.0):
    etch_elements.append((f"chim_brick_h_{y:.1f}", f"M {fmt(CHIM_X0 + 3.0)} {fmt(y)} L {fmt(CHIM_X0 + 4 * CHIM_FACE_W - 3.0)} {fmt(y)}"))
# No vertical joints below the 191 mm course: a 198+7 mm tick would poke
# past the shortest face bottoms (y = 200 on F4 and at F1's left edge).
for row, y in enumerate((177.0, 184.0, 191.0)):
    offset = 0.0 if row % 2 == 0 else 8.0
    x = CHIM_X0 + 3.0 + offset
    while x < CHIM_X0 + 4 * CHIM_FACE_W - 3.0:
        etch_elements.append((f"chim_brick_v_{y:.1f}_{x:.1f}", f"M {fmt(x)} {fmt(y)} L {fmt(x)} {fmt(y + 7.0)}"))
        x += 16.0

# --- SVG emission ---------------------------------------------------------
root = ET.Element("svg")
root.set("xmlns", SVG_NS)
root.set("width", f"{int(DOC_W)}mm")
root.set("height", f"{int(DOC_H)}mm")
root.set("viewBox", f"0 0 {int(DOC_W)} {int(DOC_H)}")

# Layer groups in order: etch, score, cut
etch_g = ET.SubElement(root, "g")
etch_g.set("id", "etch")
etch_g.set("stroke", "#000000")
etch_g.set("stroke-width", "0.2")
etch_g.set("fill", "none")

score_g = ET.SubElement(root, "g")
score_g.set("id", "score")
score_g.set("stroke", "#0000FF")
score_g.set("stroke-width", "0.2")
score_g.set("fill", "none")

cut_g = ET.SubElement(root, "g")
cut_g.set("id", "cut")
cut_g.set("stroke", "#FF0000")
cut_g.set("stroke-width", "0.2")
cut_g.set("fill", "none")

for name, d in etch_elements:
    add_path(etch_g, d, "#000000", eid=name)

for name, x1, y1, x2, y2 in score_elements:
    add_line(score_g, x1, y1, x2, y2, "#0000FF", eid=name)

for name, d in cut_elements:
    add_path(cut_g, d, "#FF0000", eid=name)

# Pretty print
rough = ET.tostring(root, encoding="unicode")
reparsed = minidom.parseString(rough)
pretty = reparsed.toprettyxml(indent="  ")
lines = [line for line in pretty.splitlines() if line.strip()]
pretty = "\n".join(lines)

out_dir = os.path.dirname(os.path.abspath(__file__))
out_path = os.path.join(out_dir, "house_a3.svg")
with open(out_path, "w") as f:
    f.write(pretty)

# --- validation ------------------------------------------------------------

def fail(msg):
    print("VALIDATION FAIL:", msg, file=sys.stderr)
    sys.exit(1)


def bbox_of_path_d(d):
    """Return a bounding box from a simple path string (M/L/Z/A)."""
    # Very small parser: only handles M, L, Z, A commands as we generate them.
    import re
    xs, ys = [], []
    tokens = re.findall(r"[MLAZ]|-?\d+\.?\d*", d)
    i = 0
    while i < len(tokens):
        cmd = tokens[i]
        if cmd == "M":
            i += 1
            xs.append(float(tokens[i])); ys.append(float(tokens[i + 1]))
            i += 2
        elif cmd == "L":
            i += 1
            xs.append(float(tokens[i])); ys.append(float(tokens[i + 1]))
            i += 2
        elif cmd == "A":
            # A rx ry xrot large sweep ex ey
            i += 1
            rx = float(tokens[i]); ry = float(tokens[i + 1])
            i += 5  # skip xrot, large, sweep
            ex = float(tokens[i]); ey = float(tokens[i + 1])
            i += 2
            # Include endpoint and ellipse bbox extremes
            xs.append(ex); ys.append(ey)
            # Approximate: include center+/-radius (good enough for our small circles)
            # Real center is not trivial to extract, but for full/semicircles this is safe.
            cx_approx = (xs[-2] + ex) / 2.0 if len(xs) >= 2 else ex
            cy_approx = (ys[-2] + ey) / 2.0 if len(ys) >= 2 else ey
            xs.extend([cx_approx - rx, cx_approx + rx])
            ys.extend([cy_approx - ry, cy_approx + ry])
        elif cmd == "Z":
            i += 1
        else:
            i += 1
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def element_bbox(el):
    """Return bbox of a parsed SVG element."""
    tag = el.tag
    if tag.endswith("path"):
        return bbox_of_path_d(el.get("d"))
    if tag.endswith("line"):
        x1 = float(el.get("x1"))
        y1 = float(el.get("y1"))
        x2 = float(el.get("x2"))
        y2 = float(el.get("y2"))
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    if tag.endswith("rect"):
        x = float(el.get("x"))
        y = float(el.get("y"))
        w = float(el.get("width"))
        h = float(el.get("height"))
        return (x, y, x + w, y + h)
    if tag.endswith("circle"):
        cx = float(el.get("cx"))
        cy = float(el.get("cy"))
        r = float(el.get("r"))
        return (cx - r, cy - r, cx + r, cy + r)
    return None


# Parse the generated SVG back and assert group structure
tree = ET.parse(out_path)
root2 = tree.getroot()

if root2.get("width") != f"{int(DOC_W)}mm":
    fail(f"width mismatch: {root2.get('width')}")
if root2.get("height") != f"{int(DOC_H)}mm":
    fail(f"height mismatch: {root2.get('height')}")
if root2.get("viewBox") != f"0 0 {int(DOC_W)} {int(DOC_H)}":
    fail(f"viewBox mismatch: {root2.get('viewBox')}")

groups = [g for g in root2 if g.tag.endswith("g")]
if [g.get("id") for g in groups] != ["etch", "score", "cut"]:
    fail(f"layer ids wrong: {[g.get('id') for g in groups]}")
if [g.get("stroke") for g in groups] != ["#000000", "#0000FF", "#FF0000"]:
    fail(f"layer strokes wrong: {[g.get('stroke') for g in groups]}")
for g in groups:
    if g.get("stroke-width") != "0.2":
        fail(f"group {g.get('id')} stroke-width {g.get('stroke-width')}")
    if g.get("fill") != "none":
        fail(f"group {g.get('id')} fill {g.get('fill')}")

# (a) Every element bbox inside working rect
for g in groups:
    for el in g:
        b = element_bbox(el)
        if b is None:
            continue
        if (b[0] < WORK_X0 - EPS or b[1] < WORK_Y0 - EPS or
                b[2] > WORK_X1 + EPS or b[3] > WORK_Y1 + EPS):
            fail(f"{g.get('id')} element bbox {b} outside working window")

# (b) No score/etch element inside a cut aperture interior
# Aperture regions: rectangular windows, door opening, chimney slot, gable circles.
apertures = []
for name, x, y, w, h in windows:
    apertures.append(("rect", x, y, x + w, y + h))
apertures.append(("rect", DOOR_X0, DOOR_Y0, DOOR_X1, DOOR_Y1))
for sx0, sy0, sx1, sy1 in ROOF_SLOTS:
    apertures.append(("rect", sx0, sy0, sx1, sy1))
apertures.append(("circle", GABLE_WIN_CX_A, GABLE_WIN_CY, GABLE_WIN_R))
apertures.append(("circle", GABLE_WIN_CX_B, GABLE_WIN_CY, GABLE_WIN_R))

# (b0) Cut apertures (and the door) must not overlap each other: an
# overlapping pair means one cut slices into another feature.
def _ap_rect(ap):
    if ap[0] == "rect":
        return ap[1], ap[2], ap[3], ap[4]
    _, cx, cy, r = ap
    return cx - r, cy - r, cx + r, cy + r


for ai in range(len(apertures)):
    for aj in range(ai + 1, len(apertures)):
        a, b = _ap_rect(apertures[ai]), _ap_rect(apertures[aj])
        if not (a[2] <= b[0] + EPS or b[2] <= a[0] + EPS or
                a[3] <= b[1] + EPS or b[3] <= a[1] + EPS):
            fail(f"apertures overlap: {apertures[ai]} vs {apertures[aj]}")

# Score lines must not overlap each other collinearly (double scores cut
# through 350gsm card). Check horizontal/vertical segment pairs.
for si in range(len(score_elements)):
    for sj in range(si + 1, len(score_elements)):
        n1, ax0, ay0, ax1, ay1 = score_elements[si]
        n2, bx0, by0, bx1, by1 = score_elements[sj]
        if ay0 == ay1 and by0 == by1 and abs(ay0 - by0) < EPS:
            lo, hi = max(min(ax0, ax1), min(bx0, bx1)), min(max(ax0, ax1), max(bx0, bx1))
            if hi - lo > EPS:
                fail(f"overlapping horizontal scores: {n1} vs {n2}")
        if ax0 == ax1 and bx0 == bx1 and abs(ax0 - bx0) < EPS:
            lo, hi = max(min(ay0, ay1), min(by0, by1)), min(max(ay0, ay1), max(by0, by1))
            if hi - lo > EPS:
                fail(f"overlapping vertical scores: {n1} vs {n2}")

# Door-plank and handle etch is allowed to sit inside the door aperture because
# it is etched on the door panel itself.
allowed_inside_door = set()
for name, _ in etch_elements:
    if name.startswith("door_plank") or name == "door_handle":
        allowed_inside_door.add(name)


def point_inside_aperture(px, py, ap):
    if ap[0] == "rect":
        _, rx0, ry0, rx1, ry1 = ap
        return rx0 + EPS < px < rx1 - EPS and ry0 + EPS < py < ry1 - EPS
    else:  # circle
        _, cx, cy, r = ap
        return (px - cx) ** 2 + (py - cy) ** 2 < (r - EPS) ** 2


def element_touches_aperture(el, ap):
    """Check if any sampled point of an element lies strictly inside ap."""
    tag = el.tag
    if tag.endswith("path"):
        d = el.get("d")
        # Sample midpoints of L segments and centers of A arcs
        import re
        tokens = re.findall(r"[MLAZ]|-?\d+\.?\d*", d)
        i = 0
        pts = []
        while i < len(tokens):
            cmd = tokens[i]
            if cmd == "M":
                pts.append((float(tokens[i + 1]), float(tokens[i + 2])))
                i += 3
            elif cmd == "L":
                pts.append((float(tokens[i + 1]), float(tokens[i + 2])))
                i += 3
            elif cmd == "A":
                i += 7  # skip rx,ry,xrot,large,sweep,ex,ey
            elif cmd == "Z":
                i += 1
            else:
                i += 1
        # sample midpoints between consecutive points
        for j in range(len(pts) - 1):
            mx = (pts[j][0] + pts[j + 1][0]) / 2.0
            my = (pts[j][1] + pts[j + 1][1]) / 2.0
            if point_inside_aperture(mx, my, ap):
                return True
    elif tag.endswith("line"):
        x1, y1 = float(el.get("x1")), float(el.get("y1"))
        x2, y2 = float(el.get("x2")), float(el.get("y2"))
        mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        return point_inside_aperture(mx, my, ap)
    return False


for g in groups:
    if g.get("id") not in ("score", "etch"):
        continue
    for el in g:
        name = el.get("id")
        for ap in apertures:
            if ap[0] == "rect" and ap[1] == DOOR_X0 and ap[2] == DOOR_Y0 and name in allowed_inside_door:
                continue
            if element_touches_aperture(el, ap):
                fail(f"{g.get('id')} element {name} inside aperture {ap}")

# (c) Three separate piece bboxes are at least 5 mm apart
pieces = {
    "wall-strip": (WALL_LEFT, GABLE_PEAK_Y, GLUE_TAB_X1, WALL_BOTTOM + TAB_DEPTH),
    "roof": (ROOF_X0, ROOF_Y0, ROOF_X1, ROOF_Y1),
    "chimney": (CHIM_X0, CHIM_TOP_Y, CHIM_X1, F1_tongue_bottom_y),
}

piece_names = list(pieces)
for i in range(len(piece_names)):
    for j in range(i + 1, len(piece_names)):
        a = pieces[piece_names[i]]
        b = pieces[piece_names[j]]
        dx = max(a[0] - b[2], b[0] - a[2], 0)
        dy = max(a[1] - b[3], b[1] - a[3], 0)
        if max(dx, dy) < 5.0 - EPS:
            fail(f"pieces {piece_names[i]}/{piece_names[j]} closer than 5 mm")

# (d) Exact layer ids/colours already checked above.

# (e) No two cut segments coincident/overlapping (double-cut guard)

def segment_key(s):
    """Canonical key for a line segment; detects exact overlap."""
    a, b = s
    a = (round(a[0], 3), round(a[1], 3))
    b = (round(b[0], 3), round(b[1], 3))
    return tuple(sorted((a, b)))


def segments_coincide(s1, s2):
    """Return True if two segments share more than an endpoint."""
    (a1, a2), (b1, b2) = s1, s2

    def on_segment(p, q, r):
        # q lies on segment pr (collinear)
        if (min(p[0], r[0]) - EPS <= q[0] <= max(p[0], r[0]) + EPS and
                min(p[1], r[1]) - EPS <= q[1] <= max(p[1], r[1]) + EPS):
            # cross product zero
            cross = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
            return abs(cross) < EPS
        return False

    def collinear(a, b, c, d):
        return (on_segment(a, b, c) and on_segment(a, b, d)) or \
               (on_segment(c, d, a) and on_segment(c, d, b))

    # Check if they are collinear and overlap
    if collinear(a1, a2, b1, b2):
        # Project onto x or y depending on orientation
        if abs(a2[0] - a1[0]) > EPS:
            a_lo = min(a1[0], a2[0])
            a_hi = max(a1[0], a2[0])
            b_lo = min(b1[0], b2[0])
            b_hi = max(b1[0], b2[0])
        else:
            a_lo = min(a1[1], a2[1])
            a_hi = max(a1[1], a2[1])
            b_lo = min(b1[1], b2[1])
            b_hi = max(b1[1], b2[1])
        overlap = min(a_hi, b_hi) - max(a_lo, b_lo)
        return overlap > EPS
    return False


def extract_segments(el):
    """Extract line segments from a parsed cut element."""
    tag = el.tag
    if tag.endswith("path"):
        d = el.get("d")
        import re
        tokens = re.findall(r"[MLAZ]|-?\d+\.?\d*", d)
        pts = []
        i = 0
        while i < len(tokens):
            cmd = tokens[i]
            if cmd == "M":
                pts.append((float(tokens[i + 1]), float(tokens[i + 2])))
                i += 3
            elif cmd == "L":
                pts.append((float(tokens[i + 1]), float(tokens[i + 2])))
                i += 3
            elif cmd == "A":
                # approximate arc as a straight chord
                i += 1
                rx = float(tokens[i]); ry = float(tokens[i + 1])
                i += 5  # skip xrot, large, sweep
                ex = float(tokens[i]); ey = float(tokens[i + 1])
                i += 2
                pts.append((ex, ey))
            elif cmd == "Z":
                i += 1
            else:
                i += 1
        segs = []
        for j in range(len(pts) - 1):
            segs.append((pts[j], pts[j + 1]))
        return segs
    if tag.endswith("line"):
        return [((float(el.get("x1")), float(el.get("y1"))),
                 (float(el.get("x2")), float(el.get("y2"))))]
    return []


cut_g2 = [g for g in groups if g.get("id") == "cut"][0]
segments = []
for el in cut_g2:
    segments.extend(extract_segments(el))

for i in range(len(segments)):
    for j in range(i + 1, len(segments)):
        if segments_coincide(segments[i], segments[j]):
            fail(f"coincident cut segments {segments[i]} and {segments[j]}")

# --- summary ---------------------------------------------------------------
print("OK: house_a3.svg generated and validated")
for g in groups:
    print(f"  {g.get('id')}: {len(list(g))} elements")
for n, b in pieces.items():
    print(f"  piece {n}: bbox {b}")
print(f"  output: {out_path}")
