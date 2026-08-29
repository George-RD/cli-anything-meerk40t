"""Temporary implementation runner for issue #59.

Used only on the feature branch to execute the repository's red-green workflow
inside GitHub Actions. The runner is removed before the pull request is opened.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

TEST_PATH = ROOT / "cli_anything/meerk40t/tests/test_integration_status.py"
SEAM_PATH = ROOT / "cli_anything/meerk40t/utils/meerk40t_integration.py"
DEVICE_PATH = ROOT / "cli_anything/meerk40t/core/device.py"
CONTROL_PATH = ROOT / "cli_anything/meerk40t/mk_control.py"


def _replace_once(text: str, old: str, new: str, *, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"expected exactly one {label} marker, found {text.count(old)}")
    return text.replace(old, new, 1)


def _replace_region(text: str, start: str, end: str, replacement: str, *, label: str) -> str:
    try:
        start_at = text.index(start)
        end_at = text.index(end, start_at)
    except ValueError as exc:
        raise RuntimeError(f"could not locate {label} region") from exc
    return text[:start_at] + replacement + text[end_at:]


def write_tests() -> None:
    TEST_PATH.write_text(
        '''"""Real-kernel tracer tests for the shared MeerK40t status integration seam."""
from __future__ import annotations

import json
import uuid

import pytest

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
        for line in lines
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
''',
        encoding="utf-8",
    )


def write_seam() -> None:
    SEAM_PATH.write_text(
        '''"""Canonical harness integration seam for MeerK40t runtime status facts.

This tracer-bullet interface intentionally covers status/introspection only.
Direct knowledge of MeerK40t device, controller, driver, elements, and spooler
attributes for that workflow lives here so headless and extension transports
consume the same facts.
"""
from __future__ import annotations

from typing import Any

from cli_anything.meerk40t.utils import serial_probe


class MeerK40tIntegration:
    """Adapt a live MeerK40t runtime to harness-owned status facts."""

    def __init__(self, device: Any, elements: Any = None):
        self._device = device
        self._elements = elements

    @classmethod
    def from_backend(cls, backend: Any) -> "MeerK40tIntegration":
        """Build the seam from the headless harness backend."""
        device = backend.device()
        try:
            elements = backend.elements
        except Exception:
            elements = None
        return cls(device, elements)

    @classmethod
    def from_kernel(cls, kernel: Any) -> "MeerK40tIntegration":
        """Build the seam from an installed/running MeerK40t kernel."""
        return cls(
            getattr(kernel, "device", None),
            getattr(kernel, "elements", None),
        )

    @staticmethod
    def _connection_state(device: Any) -> bool:
        controller = getattr(device, "controller", None)
        connection = (
            getattr(controller, "connection", None) if controller is not None else None
        )
        if connection is None:
            return False
        connected = getattr(connection, "connected", None)
        if isinstance(connected, bool):
            return connected
        is_connected = getattr(connection, "is_connected", None)
        if callable(is_connected):
            return bool(is_connected())
        if isinstance(connected, int):
            return bool(connected)
        return bool(connected)

    @staticmethod
    def _grbl_state(device: Any) -> str | None:
        state = getattr(device, "_state", None)
        parsed_base, parsed_sub = serial_probe.parse_grbl_state(state or "")
        if parsed_base is None:
            driver = getattr(device, "driver", None)
            if driver is not None:
                state = getattr(driver, "grbl_state", None) or getattr(
                    driver, "_state", None
                )
                parsed_base, parsed_sub = serial_probe.parse_grbl_state(state or "")
        if parsed_base is None:
            return None
        return parsed_base if parsed_sub is None else f"{parsed_base}:{parsed_sub}"

    @staticmethod
    def _tree_count(elements: Any, accessor: str) -> int:
        if elements is None:
            return 0
        try:
            return len(list(getattr(elements, accessor)()))
        except Exception:
            return 0

    @staticmethod
    def _spooler_queue(device: Any) -> int:
        spooler = getattr(device, "spooler", None)
        if spooler is None:
            return 0
        try:
            return len(spooler)
        except Exception:
            return 0

    def status_snapshot(self) -> dict[str, Any]:
        """Return canonical facts consumed by both status transports."""
        device = self._device
        if device is None:
            return {
                "device": None,
                "type": None,
                "label": None,
                "serial_port": None,
                "baud": None,
                "has_serial_port": False,
                "has_baud_rate": False,
                "connected": False,
                "bed_width": None,
                "bed_height": None,
                "grbl_state": None,
                "element_count": self._tree_count(self._elements, "elems"),
                "operation_count": self._tree_count(self._elements, "ops"),
                "spooler_queue": 0,
            }

        has_serial_port = hasattr(device, "serial_port")
        serial_port = getattr(device, "serial_port", None)
        if serial_port is None:
            serial_port = getattr(device, "port", None)
        has_baud_rate = hasattr(device, "baud_rate")

        return {
            "device": str(device),
            "type": getattr(device, "name", None),
            "label": getattr(device, "label", None),
            "serial_port": serial_port,
            "baud": getattr(device, "baud_rate", None),
            "has_serial_port": has_serial_port,
            "has_baud_rate": has_baud_rate,
            "connected": self._connection_state(device),
            "bed_width": getattr(device, "bedwidth", None),
            "bed_height": getattr(device, "bedheight", None),
            "grbl_state": self._grbl_state(device),
            "element_count": self._tree_count(self._elements, "elems"),
            "operation_count": self._tree_count(self._elements, "ops"),
            "spooler_queue": self._spooler_queue(device),
        }
''',
        encoding="utf-8",
    )


def patch_device() -> None:
    text = DEVICE_PATH.read_text(encoding="utf-8")
    text = _replace_once(
        text,
        "from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend\n",
        "from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend\n"
        "from cli_anything.meerk40t.utils.meerk40t_integration import MeerK40tIntegration\n",
        label="device integration import",
    )
    text = _replace_region(
        text,
        "def _active_info(dev):",
        "def _parse_position(lines):",
        '''def _active_info(snapshot):
    """Map canonical integration facts into the existing CLI status shape."""
    if snapshot["device"] is None:
        return None
    info = {
        "device": snapshot["device"],
        "type": snapshot["type"],
        "label": snapshot["label"],
    }
    if snapshot["has_serial_port"]:
        info["port"] = snapshot["serial_port"]
    if snapshot["has_baud_rate"]:
        info["baud"] = snapshot["baud"]
    info["connected"] = snapshot["connected"]
    return info


''',
        label="device active-info",
    )
    text = _replace_region(
        text,
        "def list_devices(backend):",
        "def device_status(backend):",
        '''def list_devices(backend):
    """List providers and the active device through the shared status seam."""
    out = backend.run(_LIST_ECHO)
    lines = [l for l in out if l.strip() and not l.strip().startswith(_LIST_ECHO)]
    snapshot = MeerK40tIntegration.from_backend(backend).status_snapshot()
    return {"devices": lines, "active": _active_info(snapshot)}


''',
        label="device list",
    )
    text = _replace_region(
        text,
        "def device_status(backend):",
        "def device_info(backend):",
        '''def device_status(backend):
    """Show active-device status using canonical MeerK40t integration facts."""
    pos = None
    try:
        out = backend.run("devinfo")
        pos = _parse_position(out)
    except Exception:
        pass
    snapshot = MeerK40tIntegration.from_backend(backend).status_snapshot()
    info = _active_info(snapshot)
    result = {"position": pos}
    if info:
        result.update(info)
    else:
        result["device"] = "None"
    return result


''',
        label="device status",
    )
    text = _replace_region(
        text,
        "def device_info(backend):",
        "def _confirm_spooler_idle(backend, timeout=2.0):",
        '''def device_info(backend):
    """Show raw device info plus seam-backed status facts and parsed position."""
    raw = []
    pos = None
    try:
        raw = backend.run("devinfo")
        pos = _parse_position(raw)
    except Exception:
        pass
    snapshot = MeerK40tIntegration.from_backend(backend).status_snapshot()
    info = _active_info(snapshot)
    result = {"raw": raw, "position": pos}
    if info:
        result.update(info)
    return result


''',
        label="device info",
    )
    text = _replace_region(
        text,
        "def _connect_result(dev):",
        "def connect(backend):",
        '''def _connect_result(backend):
    """Return a status dict after an open/close attempt."""
    snapshot = MeerK40tIntegration.from_backend(backend).status_snapshot()
    info = _active_info(snapshot)
    if info is None:
        info = {"connected": False, "device": "None"}
    return info


''',
        label="connect result",
    )
    text = text.replace("_connect_result(dev)", "_connect_result(backend)")
    DEVICE_PATH.write_text(text, encoding="utf-8")


def patch_control() -> None:
    text = CONTROL_PATH.read_text(encoding="utf-8")
    text = _replace_once(
        text,
        "from cli_anything.meerk40t.utils import serial_probe\n",
        "from cli_anything.meerk40t.utils.meerk40t_integration import MeerK40tIntegration\n",
        label="control integration import",
    )
    text = _replace_region(
        text,
        "def _build_status(kernel):",
        "_KIND_NORMALIZE =",
        '''def _build_status(kernel):
    snapshot = MeerK40tIntegration.from_kernel(kernel).status_snapshot()

    device_label = snapshot["label"] or snapshot["type"]
    raw_port = snapshot["serial_port"]
    serial_port = None
    if raw_port is not None and str(raw_port).lower() != "unconfigured":
        serial_port = str(raw_port)

    return {
        "protocol": PROTOCOL_VERSION,
        "devices": [device_label] if device_label else [],
        "active_device": device_label,
        "serial_port": serial_port,
        "grbl_state": snapshot["grbl_state"] or "unknown",
        "bed": {
            "width": None if snapshot["bed_width"] is None else str(snapshot["bed_width"]),
            "height": None if snapshot["bed_height"] is None else str(snapshot["bed_height"]),
        },
        "elements": snapshot["element_count"],
        "operations": snapshot["operation_count"],
        "spooler_queue": snapshot["spooler_queue"],
    }



''',
        label="attach status",
    )
    CONTROL_PATH.write_text(text, encoding="utf-8")


def implement() -> None:
    write_seam()
    patch_device()
    patch_control()


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"tests", "implementation"}:
        raise SystemExit("usage: agent_issue59.py tests|implementation")
    if sys.argv[1] == "tests":
        write_tests()
    else:
        implement()


if __name__ == "__main__":
    main()
