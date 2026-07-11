"""Serial port discovery and GRBL identification.

Discovery is stdlib-only (glob), so ``device detect`` can list candidate
ports without touching any serial transport. Probing opens a port via
pyserial (injected at the seam for tests) and parses the GRBL banner,
status report and ``$I`` identification.

All parsers are pure functions over raw response strings so they can be
unit-tested on canned GRBL 1.1 output without hardware.
"""

from __future__ import annotations

import glob
import re
import time
from typing import Callable, Optional, Sequence

# Candidate GRBL/CH340 serial paths on macOS. Globbing only — no port is opened.
SERIAL_GLOB_PATTERNS = ("/dev/cu.usbserial*", "/dev/cu.usbmodem*")

# Probe order: fastest/most-common first.
DEFAULT_BAUD_RATES: tuple[int, ...] = (115200, 230400, 57600, 9600)

# Seconds to wait after the wake sequence before reading the identification.
DEFAULT_SETTLE = 2.0

_BANNER_RE = re.compile(r"Grbl\s+([0-9][\w.]+)")
_VER_RE = re.compile(r"\[VER:([^\]]+)\]")
_STATE_RE = re.compile(r"<(Idle|Run|Jog|Alarm|Door|Check|Home|Hold|Sleep)[|>]")


def list_serial_ports() -> list[str]:
    """Return sorted candidate serial port paths.

    Uses only :func:`glob.glob` against the known macOS USB-serial device
    patterns; this never opens a port.
    """
    paths: set[str] = set()
    for pattern in SERIAL_GLOB_PATTERNS:
        paths.update(glob.glob(pattern))
    return sorted(paths)


def parse_grbl_probe(raw: str) -> dict:
    """Parse a raw GRBL identification blob into firmware/version/state.

    Handles the GRBL 1.1 connect banner (``Grbl 1.1f ['$' for help]``),
    the ``$I`` response (``[VER:1.1f.20170801:]``) and a status report
    (``<Idle|...>``).
    """
    result = {"firmware": None, "version": None, "state": None}
    if not raw:
        return result
    m = _BANNER_RE.search(raw)
    if m:
        result["firmware"] = "Grbl"
        result["version"] = m.group(1)
    m = _VER_RE.search(raw)
    if m and result["version"] is None:
        version = m.group(1).strip().split()[0].rstrip(":")
        result["version"] = version or None
    m = _STATE_RE.search(raw)
    if m:
        result["state"] = m.group(1)
    return result


def _read_all(conn) -> str:
    """Read every available byte from a serial-like connection without blocking."""
    chunks: list[bytes] = []
    while True:
        try:
            data = conn.read(4096)
        except Exception:
            break
        if not data:
            break
        chunks.append(data)
        if len(data) < 4096:
            break
    return b"".join(chunks).decode("utf-8", "replace")


def probe_port(
    path: str,
    baud_rates: Sequence[int] = DEFAULT_BAUD_RATES,
    settle: float = DEFAULT_SETTLE,
    serial_factory: Optional[Callable] = None,
) -> dict:
    """Open ``path`` at each baud rate, send the GRBL wake/identify sequence,
    and return the identification dict.

    The wake sequence is ``\\r\\n\\r\\n`` (resume/status-poll), ``?`` (status
    report) and ``$I`` (identification). The first baud rate that yields a
    recognisable GRBL response wins. Returns all-``None`` fields on total
    failure.

    ``serial_factory`` is injectable so tests can supply a fake connection
    without importing or opening real serial hardware.
    """
    if serial_factory is None:
        import serial

        serial_factory = serial.Serial

    empty = {"firmware": None, "version": None, "state": None, "baud": None}
    for baud in baud_rates:
        try:
            conn = serial_factory(path, baud, timeout=0.5)
        except Exception:
            continue
        try:
            try:
                conn.write(b"\r\n\r\n")
                conn.write(b"?\n")
                conn.write(b"$I\n")
            except Exception:
                continue
            if settle:
                time.sleep(settle)
            raw = _read_all(conn)
            parsed = parse_grbl_probe(raw)
            if parsed["firmware"] or parsed["state"]:
                parsed["baud"] = baud
                return parsed
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return empty
