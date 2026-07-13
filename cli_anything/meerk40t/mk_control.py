"""Kernel-side control commands for the cli-anything MeerK40t agent.

Exposes a framed, single-line JSON protocol on the console channel so the
CLI attach client can drive a running MeerK40t GUI/kernel headlessly without
parsing prose or relying on command ordering.

Commands registered:
  agent status
  agent stage <expected_sha256> <base64-path>

Every reply is one line::

    #CLIA1# {"key": ...}
"""

from __future__ import annotations

import json
import os
import base64
import hashlib
from cli_anything.meerk40t.utils import serial_probe

FRAME_PREFIX = "#CLIA1# "


def register(kernel):
    """Register the ``agent`` console command and its subcommands.

    Idempotent: safe to call multiple times across kernel lifecycles.
    """
    if getattr(kernel, "_cli_anything_mk_control", False):
        return
    _register_agent_command(kernel)
    kernel._cli_anything_mk_control = True


def _register_agent_command(kernel):
    def _agent_command(channel, _, args=tuple(), **kwargs):
        if not args:
            _reply(
                channel,
                {
                    "error": "usage: agent status | agent stage <expected_sha256> <base64-path>",
                },
            )
            return

        subcommand = args[0]
        if subcommand == "status":
            _reply(channel, _build_status(kernel))
        elif subcommand == "stage":
            if len(args) < 3:
                _reply(
                    channel,
                    {
                        "error": "agent stage requires <expected_sha256> <base64-path>",
                    },
                )
                return
            try:
                payload = _stage_file(kernel, args[2], expected_sha=args[1])
            except Exception as exc:  # noqa: BLE001
                payload = {"error": str(exc)}
            _reply(channel, payload)
        else:
            _reply(
                channel,
                {
                    "error": f"unknown agent subcommand: {subcommand!r}",
                },
            )

    kernel.console_command(
        "agent",
        help="CLI Anything agent control commands.",
    )(_agent_command)


def _reply(channel, payload):
    channel(FRAME_PREFIX + json.dumps(payload, separators=(",", ":")))


def _build_status(kernel):
    device = getattr(kernel, "device", None)
    elements = getattr(kernel, "elements", None)

    elem_count = 0
    op_count = 0
    if elements is not None:
        try:
            elem_count = len(list(elements.elems()))
        except Exception:
            pass
        try:
            op_count = len(list(elements.ops()))
        except Exception:
            pass

    device_label = None
    devices = []
    serial_port = None
    grbl_state = "unknown"
    bed = {"width": None, "height": None}
    spooler_queue = 0

    if device is not None:
        device_label = getattr(device, "label", None) or getattr(device, "name", None)
        if device_label:
            devices = [device_label]

        if hasattr(device, "bedwidth"):
            bed["width"] = str(device.bedwidth)
        if hasattr(device, "bedheight"):
            bed["height"] = str(device.bedheight)

        spooler = getattr(device, "spooler", None)
        if spooler is not None:
            try:
                spooler_queue = len(spooler)
            except Exception:
                pass

        raw_port = getattr(device, "serial_port", None) or getattr(device, "port", None)
        if raw_port is not None and str(raw_port).lower() != "unconfigured":
            serial_port = str(raw_port)
        # Preserve the full GRBL state vocabulary (incl. Hold/Door/Check/Home)
        # instead of collapsing unknown states to "unknown".
        state = getattr(device, "_state", None)
        parsed_base, parsed_sub = serial_probe.parse_grbl_state(state or "")
        if parsed_base is not None:
            grbl_state = parsed_base if parsed_sub is None else f"{parsed_base}:{parsed_sub}"
        else:
            driver = getattr(device, "driver", None)
            if driver is not None:
                drv_state = getattr(driver, "grbl_state", None) or getattr(
                    driver, "_state", None
                )
                parsed_base, parsed_sub = serial_probe.parse_grbl_state(drv_state or "")
                if parsed_base is not None:
                    grbl_state = parsed_base if parsed_sub is None else f"{parsed_base}:{parsed_sub}"

    return {
        "protocol": 1,
        "devices": devices,
        "active_device": device_label,
        "serial_port": serial_port,
        "grbl_state": grbl_state,
        "bed": bed,
        "elements": elem_count,
        "operations": op_count,
        "spooler_queue": spooler_queue,
    }

def _sha256_file(path: str) -> str | None:
    """Return the sha256 hex digest of a file, or None if it cannot be read."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(8192), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _stage_file(kernel, b64_path, expected_sha=None):
    # The CLI sends a base64-encoded absolute path as a single whitespace-free
    # token so paths containing spaces survive intact over the console channel.
    try:
        raw = base64.b64decode(b64_path, validate=True)
        path = os.path.abspath(os.path.expanduser(raw.decode("utf-8")))
    except Exception as exc:
        raise ValueError(f"invalid staged path encoding: {exc}")
    if not os.path.exists(path):
        raise FileNotFoundError(f"no such file: {path!r}")

    # Verify integrity IMMEDIATELY, before touching the scene, so a path whose
    # bytes differ from the recorded hash cannot silently mis-stage. On mismatch
    # we return an error frame and leave the scene untouched.
    actual_sha = _sha256_file(path)
    if actual_sha is None:
        raise RuntimeError(f"cannot read staged file: {path!r}")
    if expected_sha is not None and actual_sha != expected_sha:
        return {
            "error": (
                f"staged file hash {actual_sha} does not match expected "
                f"{expected_sha} - refusing to load scene"
            )
        }

    # Replace the scene with exactly the staged job. The meerk40t console
    # `load` command APPENDS a project's operations and elements, so without
    # cleanup a second stage would leave the previous job's operations live for
    # the burn - a safety hole. A pre-clear via branch-delete
    # (`operation* delete`) or the elements-service
    # `clear_elements_and_operations()` corrupts loader state on this build so
    # every subsequent load silently adds nothing. Per-node removal AFTER the
    # load is safe (the loader keeps working), so we snapshot the pre-existing
    # nodes, load the job, then remove exactly that snapshot - leaving only the
    # freshly staged job's operations and elements.
    elements = getattr(kernel, "elements", None)
    if elements is None:
        raise RuntimeError("elements service is not available")
    pre_existing = list(elements.ops()) + list(elements.elems())

    kernel.console(f"load {path}\n")

    # Strip only the nodes that pre-dated this load and still remain: where
    # `load` appends, this removes the previous job; where `load` already
    # replaces the scene, the old nodes are gone and nothing is stripped.
    # Either way the scene ends as exactly the staged job. pre_existing holds
    # live references, so the identities stay stable for the comparison.
    current_ids = {id(n) for n in list(elements.ops()) + list(elements.elems())}
    for node in pre_existing:
        if id(node) not in current_ids:
            continue
        try:
            node.remove_node()
        except Exception:
            try:
                node.remove()
            except Exception:
                pass

    elem_count = 0
    try:
        elem_count = len(list(elements.elems()))
    except Exception:
        pass

    ops_summary = []
    for op in elements.ops():
        op_type = getattr(op, "type", "") or ""
        kind = op_type.split()[-1] if op_type else "unknown"
        power = getattr(op, "power", None)
        speed = getattr(op, "speed", None)
        passes = getattr(op, "passes", None)
        try:
            op_elems = len(getattr(op, "children", []))
        except Exception:
            op_elems = 0
        ops_summary.append(
            {
                "kind": kind,
                "power": power,
                "speed": speed,
                "passes": passes,
                "elements": op_elems,
            }
        )

    return {
        "loaded": path,
        "elements": elem_count,
        "operations": ops_summary,
    }
