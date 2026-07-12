"""Thin client for the cli-anything MeerK40t consoleserver control channel."""

from __future__ import annotations

import json
import socket
import time

FRAME_PREFIX = "#CLIA1# "


class AttachError(Exception):
    """Raised when a framed response cannot be obtained or parsed."""

    def __init__(self, message: str, raw_tail: str | None = None):
        super().__init__(message)
        self.raw_tail = raw_tail


def _remaining_timeout(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise socket.timeout()
    return remaining


def send(host: str, port: int, command: str, timeout: float = 5.0) -> dict:
    """Send a command to the consoleserver and return the first #CLIA1# JSON frame.

    Pending bytes are drained before the command is sent so that stale output from
    previous commands does not shadow the reply.
    """
    deadline = time.monotonic() + timeout if timeout is not None else None

    sock = socket.create_connection(
        (host, port), timeout=_remaining_timeout(deadline)
    )
    try:
        # Drain any bytes already in the consoleserver buffer with a short timeout.
        drain_timeout = 0.1
        if deadline is not None:
            drain_timeout = min(drain_timeout, _remaining_timeout(deadline))
        sock.settimeout(drain_timeout)
        try:
            while True:
                try:
                    chunk = sock.recv(4096)
                except (socket.timeout, BlockingIOError):
                    break
                if not chunk:
                    break
        finally:
            sock.settimeout(_remaining_timeout(deadline))

        sock.sendall((command + "\n").encode("utf-8"))

        buffer = b""
        while True:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                line = line.decode("utf-8", errors="replace")
                # The consoleserver decorates relayed lines with leading
                # whitespace; the frame sentinel is still the line prefix once
                # that decoration is stripped.
                stripped = line.lstrip()
                if stripped.startswith(FRAME_PREFIX):
                    payload = stripped[len(FRAME_PREFIX) :]
                    try:
                        return json.loads(payload)
                    except json.JSONDecodeError as exc:
                        tail = payload + "\n" + buffer.decode("utf-8", errors="replace")
                        raise AttachError(
                            "no #CLIA1# frame received - is the GUI running with the cli-anything extension?",
                            raw_tail=tail,
                        ) from exc

        tail = buffer.decode("utf-8", errors="replace")
        raise AttachError(
            "no #CLIA1# frame received - is the GUI running with the cli-anything extension?",
            raw_tail=tail,
        )
    finally:
        sock.close()
