"""Element CRUD operations — delegates to the real MeerK40t kernel console."""

from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend


def _color_to_str(color):
    if color is None:
        return None
    s = str(color)
    return s if s and s.lower() != "none" else None


def _suffix(stroke, fill):
    """Build the stroke/fill suffix in MeerK40t console syntax.

    The console uses `stroke <color> fill <color>` inline (NOT -s/-f flags,
    and NOT pipe-separated — `|` is the command separator).
    """
    parts = []
    if stroke:
        parts.append(f"stroke {stroke}")
    if fill:
        parts.append(f"fill {fill}")
    return " " + " ".join(parts) if parts else ""


def _geom_for(node):
    """Extract type-specific geometry attrs from a node as a dict."""
    t = node.type
    g = {}
    if "rect" in t:
        for k in ("x", "y", "width", "height"):
            g[k] = getattr(node, k, None)
    elif "ellipse" in t or "circle" in t:
        for k in ("cx", "cy", "rx", "ry"):
            g[k] = getattr(node, k, None)
    elif "polyline" in t or "polygon" in t:
        pts = getattr(node, "points", None)
        if pts:
            g["points"] = [
                [_to_json_serializable(p[0]), _to_json_serializable(p[1])]
                for p in pts
            ]
        else:
            g["points"] = None
    elif "line" in t:
        for k in ("x1", "y1", "x2", "y2"):
            g[k] = getattr(node, k, None)
    elif "path" in t:
        d = getattr(node, "d", None)
        g["d"] = str(d)[:80] if d is not None else None
    elif "text" in t:
        txt = getattr(node, "text", None)
        g["text"] = (txt[:80] if txt else None)
    elif "image" in t:
        for k in ("width", "height"):
            g[k] = getattr(node, k, None)
    return g


def _to_json_serializable(obj):
    if obj is None:
        return None
    if hasattr(obj, "item"):
        try:
            return obj.item()
        except Exception:
            pass
    if isinstance(obj, (list, tuple)):
        return [_to_json_serializable(v) for v in obj]
    try:
        return float(obj)
    except (TypeError, ValueError):
        pass
    return str(obj)


def _add(backend, cmd, kind):
    """Run an add command and verify the element count actually increased."""
    before = backend.elem_count()
    backend.run(cmd)
    after = backend.elem_count()
    return {
        "added": after > before,
        "type": kind,
        "total_elements": after,
    }


def add_circle(backend, cx, cy, r, stroke=None, fill=None):
    cmd = f"circle {cx} {cy} {r}" + _suffix(stroke, fill)
    return _add(backend, cmd, "circle")


def add_rect(backend, x, y, w, h, stroke=None, fill=None):
    cmd = f"rect {x} {y} {w} {h}" + _suffix(stroke, fill)
    return _add(backend, cmd, "rect")


def add_ellipse(backend, cx, cy, rx, ry, stroke=None, fill=None):
    cmd = f"ellipse {cx} {cy} {rx} {ry}" + _suffix(stroke, fill)
    return _add(backend, cmd, "ellipse")


def add_line(backend, x1, y1, x2, y2, stroke=None, fill=None):
    cmd = f"line {x1} {y1} {x2} {y2}" + _suffix(stroke, fill)
    return _add(backend, cmd, "line")


def add_polyline(backend, points, stroke=None, fill=None):
    flat = " ".join(str(coord) for pt in points for coord in pt)
    cmd = f"polyline {flat}" + _suffix(stroke, fill)
    return _add(backend, cmd, "polyline")


def add_text(backend, x, y, text):
    """Create a text element at (x, y).

    The MeerK40t `text` console command only takes the text string, so the
    element is translated to the requested position after creation.
    """
    escaped = text.replace('"', '\\"')
    before = backend.elem_count()
    backend.run(f'text "{escaped}"')
    after = backend.elem_count()
    if after > before and (x is not None or y is not None):
        try:
            node = backend.elems()[-1]
            from meerk40t.core.units import Length
            matrix = node.matrix
            dx = Length(x).native if x is not None else 0
            dy = Length(y).native if y is not None else 0
            matrix.post_translate(dx, dy)
            node.matrix = matrix
            node.altered()
        except Exception:
            pass
    return {"added": after > before, "type": "text", "total_elements": after}


def list_elements(backend):
    result = []
    for node in backend.elems():
        geom = _geom_for(node)
        geom = {k: _to_json_serializable(v) for k, v in geom.items()}
        result.append({
            "index": len(result),
            "id": getattr(node, "id", None),
            "type": node.type,
            "stroke": _color_to_str(getattr(node, "stroke", None)),
            "fill": _color_to_str(getattr(node, "fill", None)),
            "geometry": geom,
        })
    return result


def delete_element(backend, index):
    before = backend.elem_count()
    backend.run(f"element{index} delete")
    after = backend.elem_count()
    return {"deleted": after < before, "index": index, "total_elements": after}


def select_element(backend, index):
    backend.run(f"element{index} select")
    return {"selected": True, "index": index}


def clear_elements(backend):
    backend.run("elements clear all")
    backend.run("tree clear")
    if backend.elem_count() > 0:
        try:
            backend.elements.clear_elements()
        except Exception:
            pass
    if backend.elem_count() > 0:
        for node in list(backend.elems()):
            try:
                node.remove_node()
            except Exception:
                try:
                    node.remove()
                except Exception:
                    pass
    return {"cleared": True, "total_elements": backend.elem_count()}


def frame(backend):
    before = backend.elem_count()
    backend.run("frame")
    after = backend.elem_count()
    return {"framed": after > before, "total_elements": after}


def translate_element(backend, index, tx, ty, absolute=False):
    elems = backend.elems()
    if not (0 <= index < len(elems)):
        return {"translated": False, "error": f"Index {index} out of range", "index": index}
    
    node = elems[index]
    before_x = node.matrix.value_trans_x()
    before_y = node.matrix.value_trans_y()
    
    backend.run(f"element{index} select")
    cmd = "translate"
    if absolute:
        cmd += " -a"
    cmd += f" {tx} {ty}"
    backend.run(cmd)
    
    after_x = node.matrix.value_trans_x()
    after_y = node.matrix.value_trans_y()
    
    changed = (after_x != before_x) or (after_y != before_y)
    return {
        "translated": changed or (tx == "0" and ty == "0"),
        "index": index,
        "before": {"x": before_x, "y": before_y},
        "after": {"x": after_x, "y": after_y},
        "absolute": absolute,
    }


def scale_element(backend, index, scale_x, scale_y=None, absolute=False, px=None, py=None):
    elems = backend.elems()
    if not (0 <= index < len(elems)):
        return {"scaled": False, "error": f"Index {index} out of range", "index": index}
        
    node = elems[index]
    before_sx = node.matrix.value_scale_x()
    before_sy = node.matrix.value_scale_y()
    
    backend.run(f"element{index} select")
    cmd = f"scale {scale_x}"
    if scale_y is not None:
        cmd += f" {scale_y}"
    if absolute:
        cmd += " -a"
    if px is not None:
        cmd += f" -x {px}"
    if py is not None:
        cmd += f" -y {py}"
    backend.run(cmd)
    
    after_sx = node.matrix.value_scale_x()
    after_sy = node.matrix.value_scale_y()
    
    changed = (after_sx != before_sx) or (after_sy != before_sy)
    return {
        "scaled": changed or (scale_x == "1" and (scale_y is None or scale_y == "1")),
        "index": index,
        "before": {"scale_x": before_sx, "scale_y": before_sy},
        "after": {"scale_x": after_sx, "scale_y": after_sy},
        "absolute": absolute,
    }


def rotate_element(backend, index, angle, absolute=False, cx=None, cy=None):
    elems = backend.elems()
    if not (0 <= index < len(elems)):
        return {"rotated": False, "error": f"Index {index} out of range", "index": index}
        
    node = elems[index]
    before_rot = float(node.matrix.rotation)
    
    backend.run(f"element{index} select")
    cmd = f"rotate {angle}"
    if absolute:
        cmd += " -a"
    if cx is not None:
        cmd += f" -x {cx}"
    if cy is not None:
        cmd += f" -y {cy}"
    backend.run(cmd)
    
    after_rot = float(node.matrix.rotation)
    
    changed = (after_rot != before_rot)
    return {
        "rotated": changed or angle == "0deg",
        "index": index,
        "before_rotation": before_rot,
        "after_rotation": after_rot,
        "absolute": absolute,
    }


def align_elements(backend, mode, indexes=None):
    all_nodes = list(backend.elems())
    if indexes:
        selected = [all_nodes[i] for i in indexes if 0 <= i < len(all_nodes)]
    else:
        selected = all_nodes
        
    if not selected:
        return {"aligned": False, "error": "No elements selected", "mode": mode}
        
    backend.elements.set_emphasis(selected)
    
    before_bounds = []
    for node in selected:
        try:
            before_bounds.append(tuple(node.bounds) if node.bounds else None)
        except Exception:
            before_bounds.append(None)
            
    backend.run(f"align {mode}")
    
    after_bounds = []
    for node in selected:
        try:
            after_bounds.append(tuple(node.bounds) if node.bounds else None)
        except Exception:
            after_bounds.append(None)
            
    changed = any(b != a for b, a in zip(before_bounds, after_bounds) if b is not None and a is not None)
    return {
        "aligned": changed or len(selected) <= 1,
        "mode": mode,
        "num_elements": len(selected),
    }


def _group_count(backend):
    return sum(1 for node in backend.elements.elems_nodes() if node.type == "group")


def group_elements(backend, label=None, indexes=None):
    all_nodes = list(backend.elems())
    if indexes:
        selected = [all_nodes[i] for i in indexes if 0 <= i < len(all_nodes)]
    else:
        selected = all_nodes
        
    if not selected:
        return {"grouped": False, "error": "No elements selected", "label": label}
        
    backend.elements.set_emphasis(selected)
    
    before_count = _group_count(backend)
    
    cmd = "group"
    if label:
        cmd += f" {label}"
    backend.run(cmd)
    
    after_count = _group_count(backend)
    
    return {
        "grouped": after_count > before_count,
        "label": label,
        "num_elements": len(selected),
        "before_groups": before_count,
        "after_groups": after_count,
    }


def ungroup_elements(backend, index=None):
    all_nodes = list(backend.elements.elems_nodes())
    if index is not None:
        if 0 <= index < len(all_nodes) and all_nodes[index].type in ("group", "file"):
            groups_to_select = [all_nodes[index]]
        else:
            return {"ungrouped": False, "error": f"Node at index {index} is not a group or file"}
    else:
        groups_to_select = [node for node in all_nodes if node.type == "group"]
        
    if not groups_to_select:
        return {"ungrouped": False, "error": "No groups found to ungroup"}
        
    backend.elements.set_emphasis(groups_to_select)
    
    before_count = _group_count(backend)
    
    backend.run("ungroup")
    
    after_count = _group_count(backend)
    
    return {
        "ungrouped": after_count < before_count,
        "before_groups": before_count,
        "after_groups": after_count,
    }
