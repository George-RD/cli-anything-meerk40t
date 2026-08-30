"""Canonical harness integration seam for MeerK40t runtime behavior.

Direct knowledge of MeerK40t device, controller, driver, elements, spooler, and
signal primitives lives here so headless and extension transports consume the
same runtime facts and completion semantics.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from cli_anything.meerk40t.utils import serial_probe


class MeerK40tIntegration:
    """Adapt a live MeerK40t runtime to harness-owned integration facts."""

    def __init__(
        self,
        device: Any,
        elements: Any = None,
        *,
        kernel: Any = None,
        runner: Any = None,
    ):
        self._device = device
        self._elements = elements
        self._kernel = kernel
        self._runner = runner

    @classmethod
    def from_backend(cls, backend: Any) -> "MeerK40tIntegration":
        """Build the seam from the headless harness backend."""
        device = backend.device()
        try:
            elements = backend.elements
        except Exception:
            elements = None
        return cls(
            device,
            elements,
            kernel=getattr(backend, "kernel", None),
            runner=getattr(backend, "run", None),
        )

    @classmethod
    def from_device(cls, device: Any) -> "MeerK40tIntegration":
        """Build the seam from an already-resolved device service."""
        return cls(device)

    @classmethod
    def from_kernel(cls, kernel: Any) -> "MeerK40tIntegration":
        """Build the seam from an installed/running MeerK40t kernel."""
        return cls(
            getattr(kernel, "device", None),
            getattr(kernel, "elements", None),
            kernel=kernel,
        )

    @staticmethod
    def connection_state(connection: Any) -> bool:
        """Resolve a MeerK40t driver's live connection state."""
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

    @classmethod
    def _device_connection_state(cls, device: Any) -> bool:
        controller = getattr(device, "controller", None)
        connection = (
            getattr(controller, "connection", None)
            if controller is not None
            else None
        )
        return cls.connection_state(connection)

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

    @staticmethod
    def _spooler_is_idle(spooler: Any) -> bool | None:
        """Normalize released MeerK40t's property and legacy callable shapes."""
        if spooler is None:
            return None
        is_idle = getattr(spooler, "is_idle", None)
        try:
            if callable(is_idle):
                return bool(is_idle())
            if is_idle is not None:
                return bool(is_idle)
        except Exception:
            return None
        return None

    @staticmethod
    def _spooler_jobs(spooler: Any) -> tuple[Any, ...] | None:
        """Snapshot queued job identities when the native spooler exposes them."""
        queue = getattr(spooler, "queue", None)
        if queue is None:
            return None
        try:
            return tuple(queue)
        except Exception:
            return None

    def live_controller(self) -> tuple[Any, dict[str, Any] | None]:
        """Return the active writable live controller or a structured refusal."""
        device = self._device
        if device is None:
            return None, {"error": "no active device", "connected": False}
        controller = getattr(device, "controller", None)
        if controller is None or not hasattr(controller, "write"):
            return None, {
                "error": "active device has no writable controller",
                "connected": False,
            }
        connection = getattr(controller, "connection", None)
        try:
            connected = self.connection_state(connection)
        except Exception as exc:
            return None, {
                "error": f"connection state is indeterminate: {exc}",
                "connected": False,
                "status": "indeterminate",
            }
        if not connected:
            return None, {
                "error": "no live connection; run device connect first",
                "connected": False,
            }
        return controller, None

    def _motion_runtime(self) -> tuple[Any, Any, str, str | None]:
        """Resolve native runtime readiness as ready/busy/indeterminate/error."""
        device = self._device
        if device is None:
            return None, None, "error", "no active device"
        spooler = getattr(device, "spooler", None)
        if spooler is None or not hasattr(spooler, "command"):
            return None, None, "error", "active device has no native spooler"

        idle = self._spooler_is_idle(spooler)
        if idle is None:
            return (
                spooler,
                getattr(device, "driver", None),
                "indeterminate",
                "device spooler state is indeterminate",
            )
        if not idle:
            return (
                spooler,
                getattr(device, "driver", None),
                "busy",
                "device spooler is busy",
            )

        driver = getattr(device, "driver", None)
        if driver is not None:
            if bool(getattr(driver, "paused", False)):
                return spooler, driver, "busy", "device driver is paused"
            hold_work = getattr(driver, "hold_work", None)
            if callable(hold_work):
                try:
                    if hold_work(0):
                        return spooler, driver, "busy", "device driver is busy"
                except Exception as exc:
                    return (
                        spooler,
                        driver,
                        "indeterminate",
                        f"device driver state is indeterminate: {exc}",
                    )
        return spooler, driver, "ready", None

    @staticmethod
    def _contains_busy_output(lines: Any) -> bool:
        if not lines:
            return False
        try:
            return any("busy error" in str(line).lower() for line in lines)
        except TypeError:
            return "busy error" in str(lines).lower()

    @staticmethod
    def _motion_result(
        status: str,
        error: str | None = None,
        *,
        connected: bool | None = None,
    ) -> dict[str, Any]:
        result = {
            "status": status,
            "acknowledged": status == "completed",
            "error": error,
        }
        if connected is not None:
            result["connected"] = connected
        return result

    def _legacy_callable_completion(self, spooler: Any) -> bool:
        """Support old embedding adapters without weakening native runtime proof.

        Released MeerK40t exposes ``spooler.is_idle`` as a boolean property and
        provides the kernel signal bus used by :meth:`run_normal_motion`. Older
        harness adapters used an explicit callable ``is_idle()`` completion hook.
        Only that callable adapter shape is accepted here; native queue emptiness
        is never used as the production completion condition.
        """
        is_idle = getattr(spooler, "is_idle", None)
        if not callable(is_idle):
            return False
        try:
            return bool(is_idle())
        except Exception:
            return False

    def run_normal_motion(self, command: str, timeout: float = 2.0) -> dict[str, Any]:
        """Dispatch normal motion through MeerK40t and prove native completion.

        The command is submitted through MeerK40t's console/spooler path so its
        native unit parsing, view transforms, ordering, and device behavior stay
        authoritative. Success requires two native spooler completions: the
        submitted motion job and a subsequently queued ``wait_finish`` barrier.
        The barrier executes in the same driver stream and does not complete
        until the driver's own pending-work primitive is satisfied.

        Queue emptiness alone is not proof. A bounded timeout, native abort,
        busy/refused or indeterminate state, lost connection, or runtime
        exception is structured non-success and is never auto-retried.
        """
        try:
            _, connection_error = self.live_controller()
        except Exception as exc:
            return self._motion_result("error", str(exc))
        if connection_error is not None:
            return self._motion_result(
                connection_error.get("status", "disconnected"),
                connection_error["error"],
                connected=connection_error.get("connected", False),
            )

        spooler, _, runtime_state, runtime_reason = self._motion_runtime()
        if spooler is None:
            return self._motion_result("error", runtime_reason, connected=True)
        if runtime_state != "ready":
            return self._motion_result(runtime_state, runtime_reason, connected=True)
        if not callable(self._runner):
            return self._motion_result(
                "error", "integration seam has no command runner", connected=True
            )

        kernel = self._kernel
        listen = getattr(kernel, "listen", None) if kernel is not None else None
        unlisten = getattr(kernel, "unlisten", None) if kernel is not None else None
        process_queue = (
            getattr(kernel, "process_queue", None) if kernel is not None else None
        )
        native_signals = callable(listen) and callable(unlisten)

        # Compatibility for old harness embedding adapters. Real supported
        # MeerK40t runtimes always take the native-signal branch below.
        if not native_signals:
            try:
                lines = self._runner(command)
            except Exception as exc:
                return self._motion_result("error", str(exc), connected=True)
            if self._contains_busy_output(lines):
                return self._motion_result(
                    "busy", "MeerK40t refused motion: Busy Error", connected=True
                )
            if self._legacy_callable_completion(spooler):
                return self._motion_result("completed", connected=True)
            return self._motion_result(
                "indeterminate",
                "native motion completion signals unavailable",
                connected=True,
            )

        completed_count = 0
        completed_lock = threading.Lock()
        completion_event = threading.Event()
        aborted_event = threading.Event()
        armed = False
        completed_registered = False
        aborted_registered = False

        def _completed(origin=None, *message):
            nonlocal completed_count
            if not armed:
                return
            with completed_lock:
                completed_count += 1
            completion_event.set()

        def _aborted(origin=None, *message):
            if armed:
                aborted_event.set()
                completion_event.set()

        try:
            try:
                listen("spooler;completed", _completed)
                completed_registered = True
                listen("spooler;aborted", _aborted)
                aborted_registered = True
                # ``Kernel.listen`` is queued; activate listeners now while
                # deliberately ignoring replay of previous last-message values.
                if callable(process_queue):
                    process_queue()
            except Exception as exc:
                return self._motion_result(
                    "error",
                    f"native motion signal setup failed: {exc}",
                    connected=True,
                )

            armed = True
            try:
                lines = self._runner(command)
            except Exception as exc:
                return self._motion_result("error", str(exc), connected=True)

            if self._contains_busy_output(lines):
                return self._motion_result(
                    "busy", "MeerK40t refused motion: Busy Error", connected=True
                )

            jobs_before_barrier = self._spooler_jobs(spooler)
            try:
                spooler.command("wait_finish")
            except Exception as exc:
                return self._motion_result(
                    "error",
                    f"failed to queue native completion barrier: {exc}",
                    connected=True,
                )
            jobs_after_barrier = self._spooler_jobs(spooler)
            barrier_job = None
            if jobs_before_barrier is not None and jobs_after_barrier is not None:
                before_ids = {id(job) for job in jobs_before_barrier}
                barrier_job = next(
                    (job for job in jobs_after_barrier if id(job) not in before_ids),
                    None,
                )

            deadline = time.monotonic() + max(0.0, timeout)
            while True:
                if callable(process_queue):
                    try:
                        process_queue()
                    except Exception as exc:
                        return self._motion_result(
                            "error",
                            f"native motion signal processing failed: {exc}",
                            connected=True,
                        )
                if aborted_event.is_set():
                    return self._motion_result(
                        "aborted", "MeerK40t aborted the motion job", connected=True
                    )

                with completed_lock:
                    completed = completed_count
                barrier_pending = False
                if barrier_job is not None:
                    current_jobs = self._spooler_jobs(spooler)
                    if current_jobs is None:
                        return self._motion_result(
                            "indeterminate",
                            "native completion barrier state is indeterminate",
                            connected=True,
                        )
                    barrier_pending = any(job is barrier_job for job in current_jobs)

                if completed >= 2 and not barrier_pending:
                    try:
                        _, connection_error = self.live_controller()
                    except Exception as exc:
                        return self._motion_result("error", str(exc), connected=True)
                    if connection_error is not None:
                        return self._motion_result(
                            connection_error.get("status", "disconnected"),
                            connection_error["error"],
                            connected=connection_error.get("connected", False),
                        )
                    _, _, post_state, post_reason = self._motion_runtime()
                    if post_state != "ready":
                        return self._motion_result(
                            post_state, post_reason, connected=True
                        )
                    return self._motion_result("completed", connected=True)

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._motion_result(
                        "indeterminate",
                        "native motion completion timeout",
                        connected=True,
                    )
                completion_event.wait(min(0.02, remaining))
                completion_event.clear()
        finally:
            armed = False
            if completed_registered:
                try:
                    unlisten("spooler;completed", _completed)
                except Exception:
                    pass
            if aborted_registered:
                try:
                    unlisten("spooler;aborted", _aborted)
                except Exception:
                    pass
            if callable(process_queue):
                try:
                    process_queue()
                except Exception:
                    pass

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
            "connected": self._device_connection_state(device),
            "bed_width": getattr(device, "bedwidth", None),
            "bed_height": getattr(device, "bedheight", None),
            "grbl_state": self._grbl_state(device),
            "element_count": self._tree_count(self._elements, "elems"),
            "operation_count": self._tree_count(self._elements, "ops"),
            "spooler_queue": self._spooler_queue(device),
        }
