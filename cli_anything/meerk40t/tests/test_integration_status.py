"""Real-kernel tracer tests for the shared MeerK40t status integration seam."""
from __future__ import annotations

import json
import uuid

from cli_anything.meerk40t.core import device as device_core
from cli_anything.meerk40t.utils.attach_envelope import FRAME_PREFIX, encode_request
from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend
from cli_anything.meerk40t.utils.meerk40t_integration import MeerK40tIntegration


def _profile(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _attach_status(backend: Meerk40tBackend) -> dict:
    request_id = uuid.uuid4().hex
    token = encode_request(cmd="status", request_id=request_id)
    lines = backend.run(f"agent {token}")
    frames = [
        json.loads(line[len(FRAME_PREFIX):])
        for line in [item.lstrip() for item in lines]
        if line.startswith(FRAME_PREFIX)
    ]
    assert frames, f"no attach status frame in {lines!r}"
    frame = frames[-1]
    assert frame["request_id"] == request_id
    return frame


def _assert_transport_views_match_snapshot(backend: Meerk40tBackend) -> None:
    snapshot = MeerK40tIntegration.from_backend(backend).status_snapshot()

    headless = device_core.device_status(backend)
    assert headless["device"] == snapshot["device"]
    assert headless["type"] == snapshot["type"]
    assert headless["label"] == snapshot["label"]
    assert headless["connected"] == snapshot["connected"]
    assert "position" in headless
    if snapshot["has_serial_port"]:
        assert headless["port"] == snapshot["serial_port"]
    else:
        assert "port" not in headless
    if snapshot["has_baud_rate"]:
        assert headless["baud"] == snapshot["baud"]
    else:
        assert "baud" not in headless

    attached = _attach_status(backend)
    device_label = snapshot["label"] or snapshot["type"]
    assert attached["devices"] == ([device_label] if device_label else [])
    assert attached["active_device"] == device_label

    raw_port = snapshot["serial_port"]
    expected_port = (
        None
        if raw_port is None or str(raw_port).lower() == "unconfigured"
        else str(raw_port)
    )
    assert attached["serial_port"] == expected_port
    assert attached["grbl_state"] == (snapshot["grbl_state"] or "unknown")
    assert attached["bed"] == {
        "width": None if snapshot["bed_width"] is None else str(snapshot["bed_width"]),
        "height": None if snapshot["bed_height"] is None else str(snapshot["bed_height"]),
    }
    assert attached["elements"] == snapshot["element_count"]
    assert attached["operations"] == snapshot["operation_count"]
    assert attached["spooler_queue"] == snapshot["spooler_queue"]


def test_dummy_status_flows_through_one_real_kernel_snapshot():
    with Meerk40tBackend(
        profile=_profile("issue59_dummy"),
        ignore_settings=True,
        device="dummy",
    ) as backend:
        _assert_transport_views_match_snapshot(backend)


def test_grbl_status_preserves_headless_port_baud_and_attach_normalization():
    with Meerk40tBackend(
        profile=_profile("issue59_grbl"),
        ignore_settings=True,
        device="grbl",
        port="unconfigured",
        baud=115200,
    ) as backend:
        snapshot = MeerK40tIntegration.from_backend(backend).status_snapshot()
        assert snapshot["has_serial_port"] is True
        assert snapshot["has_baud_rate"] is True
        assert snapshot["serial_port"] == "unconfigured"
        assert snapshot["baud"] == 115200
        _assert_transport_views_match_snapshot(backend)
