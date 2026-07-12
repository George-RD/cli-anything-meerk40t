"""Kernel-side control commands for the cli-anything MeerK40t agent.

Exposes a framed, single-line JSON protocol on the console channel so the
CLI attach client can drive a running MeerK40t GUI/kernel headlessly without
parsing prose or relying on command ordering.

Commands registered:
  agent status
  agent stage <path>

Every reply is one line::

    #CLIA1# {"key": ...}
"""

from __future__ import annotations

import json
import os

FRAME_PREFIX = "#CLIA1# "

_VALID_GRBL_STATES = {"Idle", "Alarm", "Run"}


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
                    "error": "usage: agent status | agent stage <path>",
                },
            )
            return

        subcommand = args[0]
        if subcommand == "status":
            _reply(channel, _build_status(kernel))
        elif subcommand == "stage":
            if len(args) < 2:
                _reply(
                    channel,
                    {
                        "error": "agent stage requires a file path",
                    },
                )
                return
            try:
                payload = _stage_file(kernel, args[1])
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

        # Prefer the device-level grbl state; fall back to the driver.
        state = getattr(device, "_state", None)
        if state in _VALID_GRBL_STATES:
            grbl_state = state
        else:
            driver = getattr(device, "driver", None)
            if driver is not None:
                drv_state = getattr(driver, "grbl_state", None) or getattr(
                    driver, "_state", None
                )
                if drv_state in _VALID_GRBL_STATES:
                    grbl_state = drv_state

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


def _stage_file(kernel, path):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(path):
        raise FileNotFoundError(f"no such file: {path!r}")

    # Clear the current scene using the same console operations the GUI path
    # would use, then load the file via the canonical console loader.
    kernel.console("element* delete\n")
    kernel.console("operation* delete\n")
    kernel.console(f"load {path}\n")

    elements = getattr(kernel, "elements", None)
    if elements is None:
        raise RuntimeError("elements service is not available")

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
