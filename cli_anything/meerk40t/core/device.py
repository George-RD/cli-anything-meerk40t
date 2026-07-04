import re

from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend


def list_devices(backend):
    out = backend.run("device")
    lines = [l for l in out if l.strip() and not l.strip().startswith("device")]
    return {"devices": lines}


def _parse_position(lines):
    pos = None
    for line in lines:
        m = re.search(r"x=\s*([\d.\-+]+)\s*,?\s*y=\s*([\d.\-+]+)", line, re.IGNORECASE)
        if m:
            try:
                pos = {"x": float(m.group(1)), "y": float(m.group(2))}
            except ValueError:
                pos = {"x": m.group(1), "y": m.group(2)}
            break
        m = re.search(r"position[:\s]+([\d.\-+]+)\s*,\s*([\d.\-+]+)", line, re.IGNORECASE)
        if m:
            try:
                pos = {"x": float(m.group(1)), "y": float(m.group(2))}
            except ValueError:
                pos = {"x": m.group(1), "y": m.group(2)}
            break
        m = re.search(r"([\d.\-+]+)\s*,\s*([\d.\-+]+)", line)
        if m and "position" in line.lower():
            try:
                pos = {"x": float(m.group(1)), "y": float(m.group(2))}
            except ValueError:
                pos = {"x": m.group(1), "y": m.group(2)}
            break
    return pos


def device_status(backend):
    out = backend.run("devinfo")
    pos = _parse_position(out)
    return {"device": str(backend.device()), "position": pos}


def home(backend):
    backend.run("home")
    return {"homed": True}


def physical_home(backend):
    backend.run("physical_home")
    return {"physical_homed": True}


def move(backend, x, y, absolute=True):
    cmd = f"move_absolute {x} {y}" if absolute else f"move {x} {y}"
    backend.run(cmd)
    return {"moved": True, "x": x, "y": y, "absolute": absolute}


def device_info(backend):
    out = backend.run("devinfo")
    pos = _parse_position(out)
    return {"raw": out, "position": pos}
