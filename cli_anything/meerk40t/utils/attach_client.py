"""Thin client for the cli-anything MeerK40t consoleserver control channel."""

from __future__ import annotations

import json
import socket
import time

from cli_anything.meerk40t.utils.attach_envelope import (
    new_request_id,
    encode_request,
    reply_matches,
    FRAME_PREFIX,
    AttachEnvelopeError,
)


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


def send(host: str, port: int, cmd: str, manifest=None, svg=None, timeout: float = 5.0) -> dict:
    """Send a versioned, request-correlated envelope and return the matching #CLIA1# frame.

    A fresh ``request_id`` is minted per call; the reply frame must echo it
    (via :func:`reply_matches`) or it is skipped as stale/foreign output. Stale
    bytes are drained before the command is sent so previous commands' output
    does not shadow this reply.
    """
    deadline = time.monotonic() + timeout if timeout is not None else None
    request_id = new_request_id()
    try:
        token = encode_request(cmd=cmd, request_id=request_id, manifest=manifest, svg=svg)
    except AttachEnvelopeError as exc:
        raise AttachError(f"failed to build attach request: {exc}") from exc

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
            try:
                sock.settimeout(_remaining_timeout(deadline))
            except OSError:
                raise AttachError(
                    "attach request timed out before reply"
                ) from None

        # The envelope is delivered as the single argument to the `agent`
        # consoleserver command (the command name is the protocol boundary;
        # the envelope token is the argument). This replaces the legacy
        # `agent status` / `agent stage <sha> <b64>` literal-subcommand format.
        sock.sendall((f"agent {token}" + "\n").encode("utf-8"))

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
                if not stripped.startswith(FRAME_PREFIX):
                    continue
                payload = stripped[len(FRAME_PREFIX) :]
                try:
                    frame = json.loads(payload)
                except json.JSONDecodeError:
                    # Skip unparseable frames; keep scanning for our reply.
                    continue
                if reply_matches(frame, request_id=request_id):
                    return frame

        tail = buffer.decode("utf-8", errors="replace")
        raise AttachError(
            "no #CLIA1# frame received - is the GUI running with the cli-anything extension?",
            raw_tail=tail,
        )
    finally:
        sock.close()
