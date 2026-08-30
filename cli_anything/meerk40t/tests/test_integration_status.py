"""Real-kernel tracer tests for the shared MeerK40t integration seam."""
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


class _NativeConnection:
    def __init__(self, connected=True):
        self.connected = connected


class _NativeController:
    def __init__(self, connected=True):
        self.connection = _NativeConnection(connected)

    def write(self, value):
        return None


class _NativeDriver:
    paused = False

    def hold_work(self, priority):
        return False


class _SignalKernel:
    def __init__(self):
        self.listeners = {}

    def listen(self, signal, callback, lifecycle_object=None):
        self.listeners.setdefault(signal, []).append(callback)

    def unlisten(self, signal, callback, lifecycle_object=None):
        callbacks = self.listeners.get(signal, [])
        if callback in callbacks:
            callbacks.remove(callback)

    def emit(self, signal, *message):
        for callback in list(self.listeners.get(signal, [])):
            callback("test", *message)


class _NativeSpooler:
    def __init__(self, kernel, *, idle=True, complete_barrier=True, abort_barrier=False):
        self.kernel = kernel
        self.is_idle = idle
        self.complete_barrier = complete_barrier
        self.abort_barrier = abort_barrier
        self.commands = []

    def __len__(self):
        return 0 if self.is_idle else 1

    def command(self, *job):
        self.commands.append(job)
        if job == ("wait_finish",):
            if self.abort_barrier:
                self.kernel.emit("spooler;aborted")
            elif self.complete_barrier:
                self.kernel.emit("spooler;completed")


class _NativeMotionBackend:
    def __init__(
        self,
        *,
        connected=True,
        idle=True,
        complete_motion=True,
        complete_barrier=True,
        abort_barrier=False,
        runtime_error=None,
        busy_output=False,
    ):
        self.kernel = _SignalKernel()
        self.spooler = _NativeSpooler(
            self.kernel,
            idle=idle,
            complete_barrier=complete_barrier,
            abort_barrier=abort_barrier,
        )
        self._device = type(
            "NativeDevice",
            (),
            {
                "controller": _NativeController(connected),
                "driver": _NativeDriver(),
                "spooler": self.spooler,
                "label": "Native test device",
            },
        )()
        self.complete_motion = complete_motion
        self.runtime_error = runtime_error
        self.busy_output = busy_output
        self.commands = []

    def device(self):
        return self._device

    def run(self, command):
        self.commands.append(command)
        if self.runtime_error is not None:
            raise RuntimeError(self.runtime_error)
        if self.busy_output:
            return [command, "Busy Error"]
        if self.complete_motion:
            self.kernel.emit("spooler;completed")
        return [command]


# Issue #61: normal motion uses MeerK40t's native spooler/driver completion seam.
def test_native_motion_success_requires_motion_and_wait_finish_barrier():
    backend = _NativeMotionBackend()
    result = device_core.move(backend, "10mm", "20mm")

    assert result["moved"] is True
    assert result["acknowledged"] is True
    assert result["error"] is None
    assert result["command"] == "move_absolute 10mm 20mm"
    assert backend.commands == ["move_absolute 10mm 20mm"]
    assert backend.spooler.commands == [("wait_finish",)]


def test_native_physical_home_uses_same_completion_contract():
    backend = _NativeMotionBackend()
    result = device_core.physical_home(backend)

    assert result["physical_homed"] is True
    assert result["acknowledged"] is True
    assert result["command"] == "physical_home"
    assert backend.commands == ["physical_home"]
    assert backend.spooler.commands == [("wait_finish",)]


def test_native_motion_timeout_is_structured_indeterminate_not_success():
    backend = _NativeMotionBackend(complete_motion=False, complete_barrier=False)
    outcome = MeerK40tIntegration.from_backend(backend).run_normal_motion(
        "home", timeout=0.01
    )

    assert outcome["status"] == "indeterminate"
    assert outcome["acknowledged"] is False
    assert "timeout" in outcome["error"]


def test_native_motion_refuses_busy_spooler_before_dispatch():
    backend = _NativeMotionBackend(idle=False)
    outcome = MeerK40tIntegration.from_backend(backend).run_normal_motion(
        "home", timeout=0.01
    )

    assert outcome["status"] == "busy"
    assert outcome["acknowledged"] is False
    assert backend.commands == []


def test_native_motion_unknown_spooler_state_is_indeterminate_before_dispatch():
    backend = _NativeMotionBackend(idle=None)
    outcome = MeerK40tIntegration.from_backend(backend).run_normal_motion(
        "home", timeout=0.01
    )

    assert outcome["status"] == "indeterminate"
    assert outcome["acknowledged"] is False
    assert backend.commands == []


def test_native_motion_signal_setup_error_is_structured():
    backend = _NativeMotionBackend()

    def _broken_listen(*args, **kwargs):
        raise RuntimeError("listener registration failed")

    backend.kernel.listen = _broken_listen
    outcome = MeerK40tIntegration.from_backend(backend).run_normal_motion(
        "home", timeout=0.01
    )

    assert outcome["status"] == "error"
    assert outcome["acknowledged"] is False
    assert "listener registration failed" in outcome["error"]
    assert backend.commands == []


def test_native_motion_console_busy_result_is_not_success():
    backend = _NativeMotionBackend(busy_output=True)
    result = device_core.home(backend)

    assert result["homed"] is False
    assert result["acknowledged"] is False
    assert "busy" in result["error"].lower()


def test_native_motion_runtime_error_is_structured():
    backend = _NativeMotionBackend(runtime_error="transport exploded")
    result = device_core.physical_home(backend)

    assert result["physical_homed"] is False
    assert result["acknowledged"] is False
    assert "transport exploded" in result["error"]


def test_native_motion_abort_is_not_success():
    backend = _NativeMotionBackend(abort_barrier=True)
    outcome = MeerK40tIntegration.from_backend(backend).run_normal_motion(
        "home", timeout=0.01
    )

    assert outcome["status"] == "aborted"
    assert outcome["acknowledged"] is False


def test_real_kernel_dummy_motion_refuses_without_live_connection():
    with Meerk40tBackend(
        profile=_profile("issue61_dummy"),
        ignore_settings=True,
        device="dummy",
    ) as backend:
        result = device_core.home(backend)

    assert result["homed"] is False
    assert result["acknowledged"] is False
    assert result["connected"] is False
    assert result["error"]
