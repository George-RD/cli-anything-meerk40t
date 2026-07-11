"""Device control: listing, status, connect and disconnect.

All functions take a ``Meerk40tBackend`` and talk to the live MeerK40t
kernel. Connect/disconnect are grounded in the real MeerK40t source:

* A GRBL (and other serial) device exposes a ``controller`` service delegate.
* ``controller.open()`` calls ``connection.connect()``
  (``meerk40t/grbl/controller.py`` -> ``meerk40t/grbl/serial_connection.py``),
  which opens the serial port via ``serial_open(serial_port, baud_rate)``.
* ``controller.close()`` calls ``connection.disconnect()`` and closes the port.

There is no ``connect``/``disconnect`` *console* command in MeerK40t; the
connection is opened through the controller object, so we invoke those methods
directly. The ``device``/``devinfo`` *console* commands (used for listing and
status) come from ``meerk40t/device/basedevice.py`` and are registered by
the backend at boot.
"""

import re

from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend

_LIST_ECHO = "device"


def _active_info(dev):
    """Build a status dict for the active device, or None when absent."""
    if dev is None:
        return None
    info = {
        "device": str(dev),
        "type": getattr(dev, "name", None),
        "label": getattr(dev, "label", None),
    }
    if hasattr(dev, "serial_port"):
        info["port"] = getattr(dev, "serial_port", None)
    if hasattr(dev, "baud_rate"):
        info["baud"] = getattr(dev, "baud_rate", None)
    controller = getattr(dev, "controller", None)
    conn = getattr(controller, "connection", None) if controller is not None else None
    info["connected"] = _connection_state(conn)
    return info

def _connection_state(conn):
    """Resolve a device connection's live state across MeerK40t drivers.

    GRBL and the dummy device expose a boolean ``connected`` attribute, while
    Lihuiyu controllers expose ``is_connected()``. Read whichever is present so
    ``device status``/``device connect`` report the real state instead of a
    false disconnected status for the advertised Lihuiyu hardware path.
    """
    if conn is None:
        return False
    connected = getattr(conn, "connected", None)
    if isinstance(connected, bool):
        return connected
    if callable(getattr(conn, "is_connected", None)):
        return bool(conn.is_connected())
    if isinstance(connected, int):
        return bool(connected)
    return bool(connected)


def _parse_position(lines):
    pos = None
    for line in lines:
        # devinfo format: "current_x,current_y;native_x,native_y;"
        m = re.search(r"^([\d.\-+]+),([\d.\-+]+);", line)
        if m:
            try:
                pos = {"x": float(m.group(1)), "y": float(m.group(2))}
            except ValueError:
                pos = {"x": m.group(1), "y": m.group(2)}
            break
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


def list_devices(backend):
    """List the available device providers via the ``device`` console command.

    The ``device`` console command is defined in
    ``meerk40t/device/basedevice.py`` and prints the registered device
    entries (provider registry). The active device is reported separately.
    """
    out = backend.run(_LIST_ECHO)
    lines = [l for l in out if l.strip() and not l.strip().startswith(_LIST_ECHO)]
    return {"devices": lines, "active": _active_info(backend.device())}


def device_status(backend):
    """Show the active device's status (position + connection state)."""
    pos = None
    try:
        out = backend.run("devinfo")
        pos = _parse_position(out)
    except Exception:
        out = None
    info = _active_info(backend.device())
    result = {"position": pos}
    if info:
        result.update(info)
    else:
        result["device"] = str(backend.device())
    return result


def device_info(backend):
    """Show raw device info plus parsed position and connection state."""
    raw = []
    pos = None
    try:
        raw = backend.run("devinfo")
        pos = _parse_position(raw)
    except Exception:
        pass
    info = _active_info(backend.device())
    result = {"raw": raw, "position": pos}
    if info:
        result.update(info)
    return result


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


def _connect_result(dev):
    """Return a status dict after an open/close attempt."""
    info = _active_info(dev)
    if info is None:
        info = {"connected": False, "device": str(dev)}
    return info


def connect(backend):
    """Open the active device's controller/transport connection (e.g. GRBL ``controller.open()``).

    Returns a JSON-serialisable dict with ``connected``, the device label/port
    and any ``error``. When the active device has no connectable controller
    (e.g. the dummy device) an error shape is returned instead of raising.
    """
    dev = backend.device()
    if dev is None:
        return {"connected": False, "error": "no active device"}
    controller = getattr(dev, "controller", None)
    if controller is None or not hasattr(controller, "open"):
        return {
            "connected": False,
            "error": "active device has no connectable controller",
            "device": str(dev),
            "label": getattr(dev, "label", None),
        }
    try:
        controller.open()
    except Exception as exc:  # pragma: no cover - serial failure surfaced to caller
        info = _connect_result(dev)
        info["connected"] = False
        info["error"] = str(exc)
        return info
    # controller.open() may swallow the underlying serial failure: pyserial
    # raises inside the connection delegate and it is logged rather than
    # propagated, so open() can return with the connection still closed.
    # Surface that as an error instead of returning a clean status shape.
    # A genuinely open connection keeps the success shape unchanged.
    info = _connect_result(dev)
    if not info.get("connected"):
        port = info.get("port")
        suffix = f" (port={port})" if port else ""
        info["error"] = f"connection failed to open{suffix}"
    return info


def disconnect(backend):
    """Close the active device's controller/transport connection (``controller.close()``)."""
    dev = backend.device()
    if dev is None:
        return {"connected": False, "error": "no active device"}
    controller = getattr(dev, "controller", None)
    if controller is None or not hasattr(controller, "close"):
        return {
            "connected": False,
            "error": "active device has no connectable controller",
            "device": str(dev),
            "label": getattr(dev, "label", None),
        }
    try:
        controller.close()
    except Exception as exc:  # pragma: no cover - surfaced to caller
        info = _connect_result(dev)
        info["error"] = str(exc)
        return info
    return _connect_result(dev)
