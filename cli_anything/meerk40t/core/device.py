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
import time
import threading

from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend
from cli_anything.meerk40t.utils import serial_probe

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


def _confirm_spooler_idle(backend, timeout=2.0):
    """Confirm a submitted command reached the spooler idle state.

    Used by home/move as the acknowledgement signal: after enqueuing the
    command we wait (bounded) for the spooler to drain. Returns True only if
    the spooler reports idle within ``timeout``; otherwise False (indeterminate,
    no auto-retry). Falls back to the device's spooler when the backend has none.
    """
    spooler = getattr(backend, "spooler", None)
    if spooler is None:
        dev = backend.device()
        if dev is not None:
            spooler = getattr(dev, "spooler", None)
    if spooler is None or not hasattr(spooler, "is_idle"):
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if spooler.is_idle():
                return True
        except Exception:
            return False
        time.sleep(0.05)
    return False


def home(backend):
    controller, err = _require_live_connection(backend)
    if err:
        err["command"] = "home"
        return err
    backend.run("home")
    acknowledged = _confirm_spooler_idle(backend)
    return {
        "homed": acknowledged,
        "acknowledged": acknowledged,
        "command": "home",
        "error": None if acknowledged else "spooler did not reach idle within timeout",
    }


def physical_home(backend):
    controller, err = _require_live_connection(backend)
    if err:
        err["command"] = "physical_home"
        return err
    backend.run("physical_home")
    acknowledged = _confirm_spooler_idle(backend)
    return {
        "physical_homed": acknowledged,
        "acknowledged": acknowledged,
        "command": "physical_home",
        "error": None if acknowledged else "spooler did not reach idle within timeout",
    }


def move(backend, x, y, absolute=True):
    controller, err = _require_live_connection(backend)
    if err:
        err["command"] = "move"
        err["absolute"] = absolute
        return err
    cmd = f"move_absolute {x} {y}" if absolute else f"move {x} {y}"
    backend.run(cmd)
    acknowledged = _confirm_spooler_idle(backend)
    return {
        "moved": acknowledged,
        "x": x,
        "y": y,
        "absolute": absolute,
        "command": cmd,
        "acknowledged": acknowledged,
        "error": None if acknowledged else "spooler did not reach idle within timeout",
    }


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


# ── Port discovery and preflight ────────────────────────────────────────────


def detect(probe: bool = False, probe_port_fn=None) -> dict:
    """List candidate serial ports (and optionally probe each for GRBL).

    Discovery globs ``/dev/cu.usbserial*`` and ``/dev/cu.usbmodem*`` only; it
    never opens a port. With ``probe`` set, each port is probed in turn via
    ``serial_probe.probe_port`` (injectable through ``probe_port_fn`` for
    tests). Returns ``{ports:[{path, probed, firmware, version, state, baud}]}``.
    """
    paths = serial_probe.list_serial_ports()
    ports: list[dict] = []
    for path in paths:
        entry = {
            "path": path,
            "probed": False,
            "firmware": None,
            "version": None,
            "state": None,
            "baud": None,
        }
        if probe:
            entry["probed"] = True
            fn = probe_port_fn or serial_probe.probe_port
            ident = fn(path)
            entry["firmware"] = ident.get("firmware")
            entry["version"] = ident.get("version")
            entry["state"] = ident.get("state")
            entry["baud"] = ident.get("baud")
        ports.append(entry)
    return {"ports": ports}

def parse_settings(text: str) -> dict:
    """Parse a ``$$`` settings dump into ``{code: value}`` (code as int)."""
    settings: dict = {}
    if not text:
        return settings
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"\$(\d+)=(.+?)\s*$", line)
        if m:
            settings[int(m.group(1))] = m.group(2).strip()
    return settings


def parse_startup_blocks(text: str) -> dict:
    """Parse a ``$N`` startup-block dump into ``{startup_blocks:[{index, block}]}``."""
    blocks: list[dict] = []
    if not text:
        return {"startup_blocks": blocks}
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"\$N(\d+)=(.*)$", line)
        if m:
            blocks.append({"index": int(m.group(1)), "block": m.group(2).strip()})
    return {"startup_blocks": blocks}


def _query_controller_response(backend, controller, command: str, timeout: float = 1.0) -> str:
    """Best-effort capture of a controller's raw reply to ``command``.

    The GRBL controller logs every received line to a ``recv-<label>`` channel,
    so we watch that channel briefly after writing. Returns the concatenated
    raw reply (empty on no hardware or no reply).
    """
    dev = backend.device()
    if dev is None:
        return ""
    label = getattr(dev, "safe_label", None) or "GRBLDevice"
    try:
        channel = backend.kernel.channel(f"recv-{label}")
    except Exception:
        channel = None
    captured: list[str] = []

    def _watch(payload, *args, **kwargs):
        captured.append(str(payload))

    if channel is not None:
        try:
            channel.watch(_watch)
        except Exception:
            channel = None
    try:
        controller.write(command + "\n")
        if timeout:
            time.sleep(timeout)
    finally:
        if channel is not None:
            try:
                channel.unwatch(_watch)
            except Exception:
                pass
    return "\n".join(captured)


def check(backend, settings_text: str = None, startup_text: str = None) -> dict:
    """Preflight a GRBL device: connect, read ``$N`` and ``$$``, and verify.

    Returns a JSON dict with ``startup_blocks``, ``settings`` and ``checks``
    (``laser_mode_on`` = ``$32==1``, ``startup_blocks_empty``, bed travel from
    ``$130``/``$131`` and ``max_s_value`` from ``$30``). ``pass`` is ``False``
    when startup blocks are non-empty or laser mode is disabled, with a
    ``reasons`` list explaining each failure.

    ``settings_text``/``startup_text`` are injected canned responses for tests;
    when omitted the live controller reply (and ``dev.hardware_config``) is
    used. A failed connection yields a clean JSON error, never a traceback.
    """
    dev = backend.device()
    if dev is None or "grbl" not in str(dev).lower():
        return {
            "error": "check requires a grbl device (use --device grbl --port ...)",
            "pass": False,
        }
    conn = connect(backend)
    if not conn.get("connected"):
        return {
            "connected": False,
            "error": conn.get("error", "connection failed to open"),
            "pass": False,
        }
    controller = getattr(dev, "controller", None)
    if settings_text is None:
        # Prefer the parsed hardware_config populated during validation.
        hw = getattr(dev, "hardware_config", None)
        if isinstance(hw, dict) and hw:
            settings_text = "\n".join(f"${k}={v}" for k, v in hw.items())
        elif controller is not None:
            settings_text = _query_controller_response(backend, controller, "$$")
        else:
            settings_text = ""
    if startup_text is None and controller is not None:
        startup_text = _query_controller_response(backend, controller, "$N")
    settings = parse_settings(settings_text or "")
    parsed_sb = parse_startup_blocks(startup_text or "")
    startup_blocks = parsed_sb["startup_blocks"]

    laser_mode_on = settings.get(32) == "1"
    nonempty_blocks = [b for b in startup_blocks if b["block"]]
    bed_travel = {"x": settings.get(130), "y": settings.get(131)}
    checks = {
        "laser_mode_on": laser_mode_on,
        "startup_blocks_empty": len(nonempty_blocks) == 0,
        "bed_travel": bed_travel,
        "max_s_value": settings.get(30),
    }
    reasons: list[str] = []
    if not laser_mode_on:
        reasons.append("$32 (laser mode) is not 1; the beam may fire during positioning")
    if nonempty_blocks:
        reasons.append(
            f"{len(nonempty_blocks)} non-empty startup block(s); expected empty"
        )
    result = {
        "startup_blocks": startup_blocks,
        "settings": settings,
        "checks": checks,
        "pass": len(reasons) == 0,
    }
    if reasons:
        result["reasons"] = reasons
    return result


def setup_profile(
    backend,
    name,
    settings_text=None,
    ident_text=None,
    config_home=None,
    save_fn=None,
):
    """Save a user machine profile from the live device's readback.

    Runs the same readback as ``check`` (``$$`` and a ``$I`` identity query)
    and writes ``profiles/<NAME>.json`` into the user config directory. The
    profile records device, baud, bed dimensions (from ``$130``/``$131``) and
    firmware provenance. Returns a JSON dict with ``saved`` and the written
    profile, or a clean ``error`` when the connection fails.
    """
    from cli_anything.meerk40t.utils import profiles as profiles_mod

    dev = backend.device()
    if dev is None or "grbl" not in str(dev).lower():
        return {
            "error": "setup requires a grbl device (use --device grbl --port ...)",
            "saved": False,
        }
    conn = connect(backend)
    if not conn.get("connected"):
        return {
            "connected": False,
            "error": conn.get("error", "connection failed to open"),
            "saved": False,
        }
    controller = getattr(dev, "controller", None)
    if settings_text is None:
        hw = getattr(dev, "hardware_config", None)
        if isinstance(hw, dict) and hw:
            settings_text = "\n".join(f"${k}={v}" for k, v in hw.items())
        elif controller is not None:
            settings_text = _query_controller_response(backend, controller, "$$")
        else:
            settings_text = ""
    if ident_text is None and controller is not None:
        ident_text = _query_controller_response(backend, controller, "$I")
    settings = parse_settings(settings_text or "")
    ident = serial_probe.parse_grbl_probe(ident_text or "")

    # The live GRBL device exposes `baud_rate`; the MeerK40tBackend has no
    # `get` method, so read the attribute directly (never a dead backend.get()).
    baud = getattr(dev, "baud_rate", None)


    bedwidth_mm = settings.get(130)
    bedheight_mm = settings.get(131)
    # provenance is only "verified" when the live readback actually yielded
    # settings or firmware identity; an empty reply must not claim trust.
    read_settings = bool(settings)
    read_ident = bool(ident.get("firmware") or ident.get("version"))
    verified = read_settings or read_ident
    if read_ident:
        firmware = " ".join(
            p for p in (ident.get("firmware") or "Grbl", ident.get("version"))
            if p
        ).strip() or "Grbl"
    else:
        firmware = None
    profile = {
        "name": name,
        "device": "grbl",
        "baud": baud,
        "bedwidth": f"{bedwidth_mm}mm" if bedwidth_mm is not None else None,
        "bedheight": f"{bedheight_mm}mm" if bedheight_mm is not None else None,
        "has_endstops": False,
        "notes": "",
        "provenance": {
            "firmware": firmware,
            "verified": verified,
        },
    }
    save = save_fn or profiles_mod.save_user_profile
    path = save(name, profile, config_home=config_home)
    return {"saved": True, "profile": profile, "path": str(path)}


# ── Jog / goto / frame ──────────────────────────────────────────────────────


def _require_live_connection(backend):
    """Return ``(controller, None)`` when a writable live link exists, else
    ``(None, error_dict)``. Jog/goto/frame must refuse without a connection."""
    dev = backend.device()
    if dev is None:
        return None, {"error": "no active device", "connected": False}
    controller = getattr(dev, "controller", None)
    if controller is None or not hasattr(controller, "write"):
        return None, {
            "error": "active device has no writable controller",
            "connected": False,
        }
    conn = getattr(controller, "connection", None)
    if not _connection_state(conn):
        return None, {
            "error": "no live connection; run device connect first",
            "connected": False,
        }
    return controller, None


_ACK_LOCKS: dict = {}


def _is_terminal(line):
    """A GRBL line that terminates a jog command's acknowledgement."""
    s = line.strip()
    low = s.lower()
    if low.startswith("ok") or low.startswith("error:") or low.startswith("alarm:"):
        return True
    if s.startswith("<") and (
        s.startswith("<Alarm")
        or s.startswith("<Hold")
        or s.startswith("<Door")
        or s.startswith("<Check")
    ):
        return True
    return False


def _await_jog_ack(backend, controller, line, timeout=0.5):
    """Write a jog line and correlate GRBL's own ok/error reply.

    Serialized per-controller so concurrent jogs don't cross wires. Stale
    replies that arrived before this command are drained, then only a terminal
    reply (ok / error: / <Alarm|Hold|Door|Check>) is correlated. A status
    report alone (e.g. <Idle|...>) is not terminal, so the result is
    indeterminate on timeout with no auto-retry.
    """
    dev = backend.device()
    if dev is None:
        return ""
    label = getattr(dev, "safe_label", None) or "GRBLDevice"
    try:
        channel = backend.kernel.channel(f"recv-{label}")
    except Exception:
        channel = None
    captured = []

    def _watch(payload, *args, **kwargs):
        captured.append(str(payload))

    lock = _ACK_LOCKS.setdefault(id(controller), threading.Lock())
    with lock:
        if channel is not None:
            try:
                channel.watch(_watch)
            except Exception:
                channel = None
        try:
            # Drain stale replies that arrived before this command.
            captured.clear()
            controller.write(line + "\n")
            if channel is not None and timeout:
                deadline = time.time() + timeout
                while time.time() < deadline:
                    if any(_is_terminal(c) for c in captured):
                        break
                    time.sleep(0.02)
        finally:
            if channel is not None:
                try:
                    channel.unwatch(_watch)
                except Exception:
                    pass
        # Correlate only terminal lines; ignore status reports.
        reply = "\n".join(c for c in captured if _is_terminal(c))
    return reply


def _classify_ack(reply):
    """Classify a raw GRBL ack reply.

    Returns ``(status, message)`` where status is one of ``ok``, ``error``,
    ``alarm``, ``hold``, ``door``, ``check``, ``indeterminate``. Only an exact
    terminal ``ok`` is acknowledged; ``error:`` and the non-idle machine states
    ``<Alarm|Hold|Door|Check>`` are failures; status reports (``<Idle|...>``,
    ``<Run|...>``, etc.) are not terminal and leave the result indeterminate;
    an empty reply is indeterminate.
    """
    if not reply:
        return "indeterminate", None
    status = "indeterminate"
    message = None
    for raw in reply.splitlines():
        line = raw.strip()
        low = line.lower()
        if low.startswith("ok"):
            if status == "indeterminate":
                status = "ok"
        elif low.startswith("error:"):
            status = "error"
            message = line
        elif line.startswith("<Alarm") or low.startswith("alarm:"):
            status = "alarm"
            message = line
        elif line.startswith("<Hold"):
            status = "hold"
            message = line
        elif line.startswith("<Door"):
            status = "door"
            message = line
        elif line.startswith("<Check"):
            status = "check"
            message = line
    return status, message


def _parse_ack(reply):
    """Return ``(acknowledged, error_text)``; preserves the jog/goto/frame 2-tuple."""
    status, message = _classify_ack(reply)
    return status == "ok", message

def _format_jog(mode: str, x: float, y: float, feed: int, machine_coords: bool = False) -> str:
    """Format a GRBL jog command per the GRBL 1.1 jogging spec.

    ``mode`` is ``G91`` (relative) or ``G90`` (absolute). Coordinates are
    machine millimetres (origin front-left, +Y away from the operator).

    ``machine_coords=False`` (jog, relative): ``$J=G21G91 ...`` forces
    millimetres and the relative frame so the current unit mode or a work
    offset cannot corrupt the move.  ``machine_coords=True`` (goto/frame,
    absolute): ``$J=G53G21G90 ...`` adds ``G53`` to address the machine
    coordinate system, never the active work offset.
    """
    prefix = "G53G21" if machine_coords else "G21"
    return f"$J={prefix}{mode} X{x} Y{y} F{feed}"



def jog(backend, dx: float, dy: float, feed: int = 600) -> dict:
    """Relative jog in machine mm (origin front-left, +Y away). Refuses when
    no live connection exists. Reports GRBL acknowledgement (ok/error)."""
    controller, err = _require_live_connection(backend)
    if err:
        err["command"] = "jog"
        return err
    line = _format_jog("G91", dx, dy, feed)
    reply = _await_jog_ack(backend, controller, line)
    acknowledged, error_text = _parse_ack(reply)
    return {
        "jogged": True,
        "mode": "relative",
        "dx": dx,
        "dy": dy,
        "feed": feed,
        "command": line,
        "acknowledged": acknowledged,
        "response": reply.strip() or None,
        "error": error_text,
    }



def goto(backend, x: float, y: float, feed: int = 3000) -> dict:
    """Absolute jog in machine mm (origin front-left, +Y away). Refuses when
    no live connection exists. Reports GRBL acknowledgement (ok/error)."""
    controller, err = _require_live_connection(backend)
    if err:
        err["command"] = "goto"
        return err
    line = _format_jog("G90", x, y, feed, machine_coords=True)
    reply = _await_jog_ack(backend, controller, line)
    acknowledged, error_text = _parse_ack(reply)
    return {
        "jogged": True,
        "mode": "absolute",
        "x": x,
        "y": y,
        "feed": feed,
        "command": line,
        "acknowledged": acknowledged,
        "response": reply.strip() or None,
        "error": error_text,
    }



def frame(backend, x, y, w, h, feed=1500):
    """Dry-frame a rectangle via five absolute jogs (start corner then the
    four edges). Returns the ordered corner list that was traced. Refuses
    when no live connection exists. Reports per-corner GRBL acknowledgement and
    aborts at the first failed corner.
    """
    controller, err = _require_live_connection(backend)
    if err:
        err["command"] = "frame"
        return err
    x2 = x + w
    y2 = y + h
    corners = [
        (x, y),
        (x2, y),
        (x2, y2),
        (x, y2),
        (x, y),
    ]
    trace = []
    acknowledged = True
    error_text = None
    for cx, cy in corners:
        line = _format_jog("G90", cx, cy, feed, machine_coords=True)
        reply = _await_jog_ack(backend, controller, line)
        ok, err_line = _parse_ack(reply)
        trace.append(
            {
                "x": cx,
                "y": cy,
                "command": line,
                "acknowledged": ok,
                "error": err_line,
            }
        )
        if not ok:
            acknowledged = False
            error_text = err_line or error_text
            break  # fail-fast: do not queue further corners after a failed one
    return {
        "framed": acknowledged,
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "feed": feed,
        "corners": trace,
        "acknowledged": acknowledged,
        "error": error_text,
    }
