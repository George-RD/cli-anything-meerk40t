"""Canonical harness integration seam for MeerK40t runtime status facts.

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
    def from_device(cls, device: Any) -> "MeerK40tIntegration":
        """Build the seam from an already-resolved device service."""
        return cls(device)

    @classmethod
    def from_kernel(cls, kernel: Any) -> "MeerK40tIntegration":
        """Build the seam from an installed/running MeerK40t kernel."""
        return cls(
            getattr(kernel, "device", None),
            getattr(kernel, "elements", None),
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
