"""Kernel-side control commands for the cli-anything MeerK40t agent.

Exposes a framed, single-line JSON protocol on the console channel so the
CLI attach client can drive a running MeerK40t GUI/kernel headlessly without
parsing prose or relying on command ordering.

Commands carried by the versioned envelope (see ``attach_envelope``):
  status       - device/bed/element/op status; the reply echoes the request's
                 ``request_id`` and ``v``.
  stage        - load the job SVG carried inside the envelope bytes, after a
                 sha256 integrity check against the manifest. The receiver
                 never receives, reads, or interpolates a filesystem path.

Requests arrive as a single base64 envelope token; every reply is one
``#CLIA1#`` frame that echoes the request's ``request_id`` and ``v``.
"""

from __future__ import annotations

import json
import os
import hashlib
import tempfile
from cli_anything.meerk40t.utils import serial_probe
from cli_anything.meerk40t.utils.attach_envelope import (
    PROTOCOL_VERSION,
    decode_request,
    format_reply,
    AttachEnvelopeError,
)


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
            channel(format_reply(None, error="usage: agent <envelope-token>"))
            return
        try:
            req = decode_request(args[0])
        except AttachEnvelopeError as exc:
            channel(format_reply(None, error=str(exc)))
            return
        request_id = req.get("request_id")
        cmd = req.get("cmd")
        if req.get("v") != PROTOCOL_VERSION:
            channel(
                format_reply(
                    request_id,
                    error=(
                        f"unsupported protocol version {req.get('v')} "
                        f"— expected {PROTOCOL_VERSION}"
                    ),
                )
            )
            return
        if cmd == "status":
            channel(format_reply(request_id, **_build_status(kernel)))
        elif cmd == "stage":
            try:
                payload = _stage_file(kernel, req.get("svg"), req.get("manifest"))
            except Exception as exc:  # noqa: BLE001
                payload = {"error": str(exc)}
            channel(format_reply(request_id, **payload))
        else:
            channel(format_reply(request_id, error=f"unknown envelope command: {cmd!r}"))

    kernel.console_command(
        "agent",
        help="CLI Anything agent control commands.",
    )(_agent_command)




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



def _stage_file(kernel, svg_bytes, manifest_bytes):
    # The CLI sends the job SVG and manifest as raw envelope bytes; the receiver
    # never receives, reads, or interpolates a filesystem path. Integrity is
    # verified against the manifest's recorded sha256 before the scene is touched.
    if not svg_bytes:
        raise ValueError("stage envelope missing svg bytes")
    if not manifest_bytes:
        raise ValueError("stage envelope missing manifest bytes")

    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid manifest payload: {exc}")
    expected_sha = manifest.get("files", {}).get("job_svg", {}).get("sha256")
    if expected_sha is None:
        return {
            "error": (
                "manifest missing files.job_svg.sha256 - refusing to load "
                "unverified scene"
            )
        }

    actual_sha = hashlib.sha256(svg_bytes).hexdigest()
    if actual_sha != expected_sha:
        return {
            "error": (
                f"staged svg hash {actual_sha} does not match manifest "
                f"{expected_sha} - refusing to load scene"
            )
        }

    # Write the received bytes to a receiver-owned temp file, then load from it.
    fd, temp_path = tempfile.mkstemp(suffix=".svg", prefix="mk_stage_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(svg_bytes)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise

    elements = getattr(kernel, "elements", None)
    if elements is None:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise RuntimeError("elements service is not available")

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
    try:
        pre_existing = list(elements.ops()) + list(elements.elems())

        kernel.console(f"load {temp_path}\n")

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

        result = {
            "loaded": temp_path,
            "elements": elem_count,
            "operations": ops_summary,
        }
    finally:
        # The console `load` is synchronous, so the scene is fully loaded before
        # we remove the receiver-owned temp file here.
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    return result
