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
