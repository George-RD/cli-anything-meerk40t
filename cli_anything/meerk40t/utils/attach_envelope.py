"""Versioned, request-correlated wire envelope for the attach control channel.

Both the CLI client (``utils/attach_client.py``) and the kernel receiver
(``mk_control.py``) speak this envelope so that every reply is matched to the
exact request that produced it. This is the transport/correlation foundation of
the receiver-verified attach staging work (foundational-remediation plan, Wave 3
/ issue #31, Phase 1).

The envelope is a single base64 token (no internal whitespace) carried as one
console argument, mirroring the existing single-line JSON reply framing
(``#CLIA1# {json}``). No legacy uncorrelated fallback exists: client and
receiver both speak this contract after the cutover.

Request token (client -> console)::

    base64( json {
        "v": PROTOCOL_VERSION,
        "request_id": "<32 hex>",
        "cmd": "status" | "stage",
        "manifest_b64": "<base64 of manifest bytes>" | null,
        "svg_b64": "<base64 of svg bytes>" | null,
    } )

Reply frame (console -> client)::

    "#CLIA1# " + json {
        "v": PROTOCOL_VERSION,
        "request_id": "<echoed>",
        ...payload or "error": "..."
    }

The client keeps reading framed ``#CLIA1#`` lines and returns only the frame
whose ``request_id`` (and protocol version) matches the outgoing request;
non-matching frames — including stale replies from a previous command and
frames destined for a different interleaved client — are skipped.
"""

from __future__ import annotations

import base64
import json
import secrets
from typing import Any

PROTOCOL_VERSION = 1

# Commands carried by the envelope. The receiver rejects any other value.
_COMMANDS = ("status", "stage")

# Console framing prefix shared with the receiver's reply lines.
FRAME_PREFIX = "#CLIA1# "


class AttachEnvelopeError(Exception):
    """Raised when an envelope cannot be built or decoded."""


def new_request_id() -> str:
    """Return a cryptographically random correlation id (32 hex chars)."""
    return secrets.token_hex(16)


def _b64_encode(data: bytes | None) -> str | None:
    if data is None:
        return None
    return base64.b64encode(data).decode("ascii")


def _b64_decode(value: Any) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AttachEnvelopeError("envelope field must be a base64 string")
    try:
        return base64.b64decode(value, validate=True)
    except Exception as exc:  # noqa: BLE001 - normalize to our error type
        raise AttachEnvelopeError(f"invalid base64 envelope field: {exc}") from exc


def encode_request(
    *,
    cmd: str,
    request_id: str,
    manifest: bytes | None = None,
    svg: bytes | None = None,
) -> str:
    """Build the single base64 request token for ``cmd``.

    ``cmd`` must be ``"status"`` or ``"stage"``. ``manifest``/``svg`` are the raw
    artifact bytes (or ``None``); they are base64-encoded inside the envelope so
    the receiver never receives, reads, or interpolates a filesystem path.
    """
    if cmd not in _COMMANDS:
        raise AttachEnvelopeError(f"unsupported envelope command: {cmd!r}")
    if not isinstance(request_id, str) or not request_id:
        raise AttachEnvelopeError("request_id must be a non-empty string")
    obj = {
        "v": PROTOCOL_VERSION,
        "request_id": request_id,
        "cmd": cmd,
        "manifest_b64": _b64_encode(manifest),
        "svg_b64": _b64_encode(svg),
    }
    try:
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AttachEnvelopeError(f"cannot encode envelope: {exc}") from exc
    return base64.b64encode(raw).decode("ascii")


def decode_request(token: str) -> dict:
    """Decode a request token into a normalized dict.

    Raises :class:`AttachEnvelopeError` only on a malformed token or encoding
    failure (so the receiver can still echo ``request_id`` for a version
    mismatch). The returned dict carries the raw ``v`` and ``request_id`` (which
    may be ``None`` if absent) plus decoded ``manifest``/``svg`` bytes and the
    ``cmd`` string. Callers must validate ``v`` and ``cmd`` themselves.
    """
    if not isinstance(token, str):
        raise AttachEnvelopeError("envelope token must be a string")
    try:
        raw = base64.b64decode(token, validate=True)
        obj = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise AttachEnvelopeError(f"invalid envelope token: {exc}") from exc
    if not isinstance(obj, dict):
        raise AttachEnvelopeError("envelope token must decode to an object")
    return {
        "v": obj.get("v"),
        "request_id": obj.get("request_id"),
        "cmd": obj.get("cmd"),
        "manifest": _b64_decode(obj.get("manifest_b64")),
        "svg": _b64_decode(obj.get("svg_b64")),
    }


def format_reply(request_id: str | None, **fields: Any) -> str:
    """Build a ``#CLIA1#`` reply frame that echoes ``request_id`` and ``v``."""
    payload = {"v": PROTOCOL_VERSION, "request_id": request_id, **fields}
    return FRAME_PREFIX + json.dumps(payload, separators=(",", ":"))


def reply_matches(
    frame: dict, *, request_id: str, version: int = PROTOCOL_VERSION
) -> bool:
    """True when ``frame`` is the correlated reply for this request."""
    if not isinstance(frame, dict):
        return False
    return frame.get("v") == version and frame.get("request_id") == request_id
