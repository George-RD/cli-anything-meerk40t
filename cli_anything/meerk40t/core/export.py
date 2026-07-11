import os
import re

from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend

_MOVE_RE = re.compile(r"^(?:G0|G1|G00|G01)\b", re.IGNORECASE)


def _length_mm(value):
    """Best-effort conversion of a bed dimension to millimetres (number)."""
    if value is None:
        return None
    mm = getattr(value, "mm", None)
    if isinstance(mm, (int, float)):
        return float(mm)
    if isinstance(value, str):
        m = re.match(r"(-?[\d.]+)\s*mm", value.strip())
        if m:
            return float(m.group(1))
    return None


def parse_placement_summary(gcode_text, bed_width, bed_height):
    """Parse emitted G-code for placement bounds and process values.

    Pure function over the generated file text: scans ``G0``/``G1`` motion
    lines for ``X``/``Y`` bounds, ``S`` (power) and ``F`` (feed) values, and
    reports the bed dimensions supplied by the caller.
    """
    x_vals: list[float] = []
    y_vals: list[float] = []
    s_vals: set[int] = set()
    f_vals: set[int] = set()
    for line in gcode_text.splitlines():
        line = line.strip()
        if not line or not _MOVE_RE.match(line):
            continue
        xm = re.search(r"X(-?[\d.]+)", line)
        ym = re.search(r"Y(-?[\d.]+)", line)
        sm = re.search(r"S(-?[\d.]+)", line)
        fm = re.search(r"F(-?[\d.]+)", line)
        if xm:
            x_vals.append(float(xm.group(1)))
        if ym:
            y_vals.append(float(ym.group(1)))
        if sm:
            try:
                s_vals.add(int(float(sm.group(1))))
            except ValueError:
                pass
        if fm:
            try:
                f_vals.add(int(float(fm.group(1))))
            except ValueError:
                pass
    return {
        "x_range": [min(x_vals), max(x_vals)] if x_vals else None,
        "y_range": [min(y_vals), max(y_vals)] if y_vals else None,
        "bed": {"w": _length_mm(bed_width), "h": _length_mm(bed_height)},
        "s_values": sorted(s_vals),
        "feeds": sorted(f_vals),
    }


def export_svg(backend, path, version="default"):
    backend.save_svg(path, version)
    return {
        "output": path,
        "format": "svg",
        "size_bytes": os.path.getsize(path),
        "version": version,
    }


def export_svgz(backend, path):
    return export_svg(backend, path, version="compressed")


def export_png(backend, path, dpi=300):
    renderer = backend.kernel.lookup("render-op/make_raster")
    if renderer is None:
        raise RuntimeError(
            "PNG export requires wxPython GUI (render-op/make_raster not registered in headless mode). "
            "Install wxPython and run with a display, or use SVG export instead."
        )
    backend.run(f"element* render -d {dpi}")
    raise RuntimeError(
        "PNG export to file path is not implemented for this renderer. "
        "Use SVG export or run inside the GUI."
    )


def _default_power_offenders(backend):
    """Return enabled operations still at the default power of 1000."""
    offenders = []
    try:
        ops = backend.ops()
    except Exception:
        return offenders
    for op in ops:
        if not getattr(op, "output", True):
            continue
        power = getattr(op, "power", None)
        try:
            if power is not None and int(power) == 1000:
                offenders.append({"op": type(op).__name__, "power": int(power)})
        except (TypeError, ValueError):
            continue
    return offenders


def export_gcode(backend, path, allow_full_power=False):
    """Best-effort G-code export via the GRBL plan/save_job pipeline.

    Refuses (returns a JSON error dict, not a raised exception) when any
    enabled operation is still at the default power of 1000, unless
    ``allow_full_power`` is set. Requires an active GRBL device. After export
    the generated file is parsed for a placement summary.
    """
    offenders = _default_power_offenders(backend)
    if offenders and not allow_full_power:
        return {
            "error": (
                "refusing export: enabled operation(s) still at default power "
                "1000 (full power); pass allow_full_power=True to override"
            ),
            "default_power_ops": offenders,
            "pass": False,
        }
    dev = backend.device()
    dev_name = str(dev).lower() if dev else ""
    if "grbl" not in dev_name:
        raise RuntimeError(
            "G-code export requires an active GRBL device. "
            "Activate one via the console passthrough, then retry. "
            "G-code emission requires an active device + spooler execution."
        )
    abspath = os.path.realpath(path)
    backend.run(f"plan copy preprocess validate blob save_job {abspath}")
    if not os.path.exists(abspath) or os.path.getsize(abspath) == 0:
        raise RuntimeError(
            "G-code export produced no output. "
            "Ensure there are classified operations with elements and the GRBL device is active. "
            "Use the console passthrough for full control over the active device and spooler."
        )
    with open(abspath, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    has_gcode = any(
        line.strip().startswith(("G", "M", "g", "m"))
        for line in text.splitlines()
    )
    if not has_gcode:
        raise RuntimeError(
            "G-code export produced invalid output (no G/M codes found). "
            "The file may contain log lines instead of real G-code. "
            "Use the console passthrough for full control."
        )
    bed_w = getattr(dev, "bedwidth", None)
    bed_h = getattr(dev, "bedheight", None)
    placement = parse_placement_summary(text, bed_w, bed_h)
    return {
        "output": abspath,
        "format": "gcode",
        "size_bytes": os.path.getsize(abspath),
        "valid": True,
        "placement": placement,
    }
