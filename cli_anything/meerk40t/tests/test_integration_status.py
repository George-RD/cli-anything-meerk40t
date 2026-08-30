"""Real-kernel and native-adapter tests for the shared integration seam."""
from __future__ import annotations

import json
import uuid
from unittest.mock import patch

from cli_anything.meerk40t.core import device as device_core
from cli_anything.meerk40t.utils.attach_envelope import FRAME_PREFIX, encode_request
from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend
from cli_anything.meerk40t.utils.meerk40t_integration import MeerK40tIntegration


def _profile(prefix: str) -> str:
    """Return an isolated real-kernel profile name for one test."""
    return f"{prefix}_{uuid.uuid4().hex}"


def _attach_status(backend: Meerk40tBackend) -> dict:
    """Run installed-agent status and return its correlated response frame."""
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
    """Assert headless and attach status consume the same canonical snapshot."""
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
    """Dummy headless and attach status share one real-kernel snapshot."""
    with Meerk40tBackend(
        profile=_profile("issue59_dummy"),
        ignore_settings=True,
        device="dummy",
    ) as backend:
        _assert_transport_views_match_snapshot(backend)


def test_grbl_status_preserves_headless_port_baud_and_attach_normalization():
    """GRBL status preserves serial facts while attach normalizes the port."""
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
        """Create a provider-style connection state."""
        self.connected = connected


class _NativeController:
    def __init__(self, connected=True):
        """Create a writable controller with a provider connection."""
        self.connection = _NativeConnection(connected)

    def write(self, value):
        """Expose the writable-controller capability without raw transport work."""
        return None


class _NativeDriver:
    paused = False

    def hold_work(self, priority):
        """Report that this native test driver can accept work."""
        return False


class _NativeJob:
    def __init__(self):
        """Create a minimal observable native LaserJob state."""
        self.time_started = None
        self.loops = 1
        self.loops_executed = 0

    def complete(self):
        """Mark the barrier as genuinely executed rather than merely removed."""
        self.time_started = 1.0
        self.loops_executed = 1


class _SignalContext:
    def __init__(self, path="selected"):
        """Expose the selected spooler's signal-origin path."""
        self._path = path


class _SignalKernel:
    def __init__(self):
        """Create a queued signal bus matching the real kernel listener shape."""
        self.listeners = {}
        self.pending = []

    def listen(self, signal, callback, lifecycle_object=None):
        """Register a callback for a named kernel signal."""
        self.listeners.setdefault(signal, []).append(callback)

    def unlisten(self, signal, callback, lifecycle_object=None):
        """Remove a callback from a named kernel signal."""
        callbacks = self.listeners.get(signal, [])
        if callback in callbacks:
            callbacks.remove(callback)

    def emit(self, signal, *message, origin="selected"):
        """Emit a signal immediately from the requested context origin."""
        for callback in list(self.listeners.get(signal, [])):
            callback(origin, *message)

    def defer(self, callback):
        """Queue native work for the integration loop's process_queue call."""
        self.pending.append(callback)

    def process_queue(self):
        """Process all queued callbacks in FIFO order."""
        pending, self.pending = self.pending, []
        for callback in pending:
            callback()


class _NativeSpooler:
    def __init__(self, kernel, *, idle=True, complete_barrier=True, abort_barrier=False):
        """Create a native-style spooler with observable queued job identities."""
        self.kernel = kernel
        self.context = _SignalContext()
        self.is_idle = idle
        self.complete_barrier = complete_barrier
        self.abort_barrier = abort_barrier
        self.commands = []
        self.queue = []

    def __len__(self):
        """Return the observable queue length used by status snapshots."""
        return len(self.queue) if self.is_idle else max(1, len(self.queue))

    def command(self, *job):
        """Queue a wait_finish barrier and later complete or abort it."""
        self.commands.append(job)
        if job != ("wait_finish",):
            return
        barrier = _NativeJob()
        self.queue.append(barrier)
        if not self.complete_barrier and not self.abort_barrier:
            return

        def _finish_barrier():
            """Resolve the queued barrier in the same order as native signals."""
            if self.abort_barrier:
                self.kernel.emit("spooler;aborted", origin=self.context._path)
            else:
                barrier.complete()
                self.kernel.emit("spooler;completed", origin=self.context._path)
            if barrier in self.queue:
                self.queue.remove(barrier)

        self.kernel.defer(_finish_barrier)


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
        busy_output=None,
        unrelated_abort=False,
    ):
        """Create a native test backend for normal-motion seam behavior."""
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
        self.unrelated_abort = unrelated_abort
        self.commands = []

    def device(self):
        """Return the selected native test device."""
        return self._device

    def run(self, command):
        """Dispatch one console command and emit its selected-spooler result."""
        self.commands.append(command)
        if self.runtime_error is not None:
            raise RuntimeError(self.runtime_error)
        if self.busy_output is not None:
            return self.busy_output
        if self.unrelated_abort:
            self.kernel.emit("spooler;aborted", origin="other-device")
        if self.complete_motion:
            self.kernel.emit(
                "spooler;completed",
                origin=self.spooler.context._path,
            )
        return [command]


def test_native_motion_success_requires_motion_and_wait_finish_barrier():
    """Motion succeeds only after its command and exact wait_finish barrier."""
    backend = _NativeMotionBackend()
    result = device_core.move(backend, "10mm", "20mm")

    assert result["moved"] is True
    assert result["status"] == "completed"
    assert result["acknowledged"] is True
    assert result["error"] is None
    assert result["command"] == "move_absolute 10mm 20mm"
    assert backend.commands == ["move_absolute 10mm 20mm"]
    assert backend.spooler.commands == [("wait_finish",)]


def test_native_relative_motion_uses_native_relative_command():
    """The relative public mode dispatches MeerK40t's real move_relative path."""
    backend = _NativeMotionBackend()
    result = device_core.move(backend, "10mm", "20mm", absolute=False)

    assert result["moved"] is True
    assert result["command"] == "move 10mm 20mm"
    assert backend.commands == ["move_relative 10mm 20mm"]


def test_native_physical_home_uses_same_completion_contract():
    """Physical home uses the same exact-job native completion proof."""
    backend = _NativeMotionBackend()
    result = device_core.physical_home(backend)

    assert result["physical_homed"] is True
    assert result["acknowledged"] is True
    assert result["command"] == "physical_home"
    assert backend.commands == ["physical_home"]
    assert backend.spooler.commands == [("wait_finish",)]


def test_native_motion_timeout_is_structured_indeterminate_not_success():
    """A bounded wait expires as indeterminate without retrying movement."""
    backend = _NativeMotionBackend(complete_motion=False, complete_barrier=False)
    outcome = MeerK40tIntegration.from_backend(backend).run_normal_motion(
        "home", timeout=0.01
    )

    assert outcome["status"] == "indeterminate"
    assert outcome["acknowledged"] is False
    assert "timeout" in outcome["error"]
    assert backend.commands == ["home"]


def test_native_motion_refuses_busy_spooler_before_dispatch():
    """A busy selected spooler refuses normal motion before command dispatch."""
    backend = _NativeMotionBackend(idle=False)
    outcome = MeerK40tIntegration.from_backend(backend).run_normal_motion(
        "home", timeout=0.01
    )

    assert outcome["status"] == "busy"
    assert outcome["acknowledged"] is False
    assert backend.commands == []


def test_native_motion_unknown_spooler_state_is_indeterminate_before_dispatch():
    """An unreadable spooler state refuses motion as indeterminate."""
    backend = _NativeMotionBackend(idle=None)
    outcome = MeerK40tIntegration.from_backend(backend).run_normal_motion(
        "home", timeout=0.01
    )

    assert outcome["status"] == "indeterminate"
    assert outcome["acknowledged"] is False
    assert backend.commands == []


def test_native_motion_signal_setup_error_is_structured():
    """Listener-registration failures are structured before any movement."""
    backend = _NativeMotionBackend()

    def _broken_listen(*args, **kwargs):
        """Inject a listener-registration failure."""
        raise RuntimeError("listener registration failed")

    backend.kernel.listen = _broken_listen
    outcome = MeerK40tIntegration.from_backend(backend).run_normal_motion(
        "home", timeout=0.01
    )

    assert outcome["status"] == "error"
    assert outcome["acknowledged"] is False
    assert "listener registration failed" in outcome["error"]
    assert backend.commands == []


def test_native_motion_scalar_busy_result_is_not_success():
    """A scalar Busy Error reply is detected before the completion barrier."""
    backend = _NativeMotionBackend(busy_output="Busy Error")
    result = device_core.home(backend)

    assert result["homed"] is False
    assert result["status"] == "busy"
    assert result["acknowledged"] is False
    assert "busy" in result["error"].lower()


def test_native_motion_iterable_busy_result_is_not_success():
    """An iterable Busy Error reply remains a structured busy refusal."""
    backend = _NativeMotionBackend(busy_output=["home", "Busy Error"])
    result = device_core.home(backend)

    assert result["homed"] is False
    assert result["status"] == "busy"
    assert result["acknowledged"] is False


def test_native_motion_runtime_error_is_structured():
    """A command-runner exception is returned as structured non-success."""
    backend = _NativeMotionBackend(runtime_error="transport exploded")
    result = device_core.physical_home(backend)

    assert result["physical_homed"] is False
    assert result["status"] == "error"
    assert result["acknowledged"] is False
    assert "transport exploded" in result["error"]


def test_native_motion_selected_spooler_abort_is_not_success():
    """An abort from the selected spooler stops the active motion wait."""
    backend = _NativeMotionBackend(abort_barrier=True)
    outcome = MeerK40tIntegration.from_backend(backend).run_normal_motion(
        "home", timeout=0.01
    )

    assert outcome["status"] == "aborted"
    assert outcome["acknowledged"] is False


def test_native_motion_ignores_unrelated_spooler_abort():
    """An abort from another device cannot fail or authorize this motion."""
    backend = _NativeMotionBackend(unrelated_abort=True)
    outcome = MeerK40tIntegration.from_backend(backend).run_normal_motion(
        "home", timeout=0.1
    )

    assert outcome["status"] == "completed"
    assert outcome["acknowledged"] is True


def test_spooler_snapshot_retry_recovers_one_transient_failure():
    """One transient queue-snapshot error is retried before indeterminate."""
    integration = MeerK40tIntegration.from_device(object())
    with patch.object(
        MeerK40tIntegration,
        "_spooler_jobs",
        side_effect=[None, ("barrier",)],
    ):
        jobs = integration._spooler_jobs_with_retry(object())
    assert jobs == ("barrier",)


def test_device_motion_passes_command_appropriate_timeouts():
    """Home and move pass their explicit real-machine completion bounds."""
    calls = []

    class _CaptureIntegration:
        def run_normal_motion(self, command, timeout=60.0):
            """Capture the runtime command and timeout for public callers."""
            calls.append((command, timeout))
            return {"status": "completed", "acknowledged": True, "error": None}

    with patch.object(
        MeerK40tIntegration,
        "from_backend",
        return_value=_CaptureIntegration(),
    ):
        device_core.home(object())
        device_core.physical_home(object())
        device_core.move(object(), "1mm", "2mm")

    assert calls == [
        ("home", 120.0),
        ("physical_home", 120.0),
        ("move_absolute 1mm 2mm", 60.0),
    ]


def test_real_kernel_dummy_motion_refuses_without_live_connection():
    """A real dummy kernel returns structured disconnected non-success."""
    with Meerk40tBackend(
        profile=_profile("issue61_dummy"),
        ignore_settings=True,
        device="dummy",
    ) as backend:
        result = device_core.home(backend)

    assert result["homed"] is False
    assert result["status"] == "disconnected"
    assert result["acknowledged"] is False
    assert result["connected"] is False
    assert result["error"]
