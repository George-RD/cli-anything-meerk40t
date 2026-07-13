"""Unit tests for cli-anything-meerk40t core modules.

Each test uses the real Meerk40tBackend booted headlessly. No external test
frameworks are required; only the standard library unittest is used.
"""

from __future__ import annotations

import os
import sys
import json
import shutil
import tempfile
import unittest
import unittest.mock
from unittest.mock import patch
import xml.etree.ElementTree as ET

from cli_anything.meerk40t.core import elements
from cli_anything.meerk40t.core import export
from cli_anything.meerk40t.core import operations
from cli_anything.meerk40t.core import project
from cli_anything.meerk40t.core import session
from cli_anything.meerk40t.core import device as device_mod
from cli_anything.meerk40t.utils import serial_probe
from cli_anything.meerk40t.utils import profiles as profiles_mod
from cli_anything.meerk40t.utils.meerk40t_backend import (
    Meerk40tBackend,
    BackendError,
    SaveVerificationError,
    LoadError,
)

import cli_anything.meerk40t.meerk40t_cli as cli_mod
from cli_anything.meerk40t.utils import submit as submit_mod
import urllib.parse
import subprocess

class BackendTestCase(unittest.TestCase):
    """Base class that creates and tears down a fresh backend per test."""

    def setUp(self):
        self.backend = Meerk40tBackend()
        self.backend.start()
        self.temp_dir = tempfile.mkdtemp(prefix="mk_test_")

    def tearDown(self):
        try:
            self.backend.shutdown()
        finally:
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def temp_path(self, filename):
        return os.path.join(self.temp_dir, filename)


class TestBackend(BackendTestCase):
    def test_start_shutdown(self):
        # setUp already started; backend should be usable.
        self.assertIsNotNone(self.backend.kernel)
        self.backend.shutdown()
        self.assertIsNone(self.backend._kernel)

    def test_run_captures_output(self):
        out = self.backend.run("circle 1in 1in 1in")
        self.assertIsInstance(out, list)
        # Captured output includes at least the command echo.
        self.assertGreater(len(out), 0)

    def test_save_svg_creates_valid_svg(self):
        path = self.temp_path("out.svg")
        self.backend.run("circle 1in 1in 1in")
        self.assertTrue(self.backend.save_svg(path))
        self.assertTrue(os.path.exists(path))
        self.assertGreater(os.path.getsize(path), 0)
        root = ET.parse(path).getroot()
        self.assertTrue(root.tag.endswith("svg"))

    def test_load_file(self):
        path = self.temp_path("roundtrip.svg")
        self.backend.run("circle 1in 1in 1in")
        self.assertTrue(self.backend.save_svg(path))
        self.backend.run("elements clear all")

        fresh = Meerk40tBackend()
        fresh.start()
        try:
            self.assertTrue(fresh.load_file(path))
            self.assertGreaterEqual(fresh.elem_count(), 1)
        finally:
            fresh.shutdown()

    def test_elems_after_add(self):
        before = self.backend.elem_count()
        self.backend.run("circle 1in 1in 1in")
        after = self.backend.elem_count()
        self.assertGreater(after, before)

    def test_ops_exist(self):
        self.backend.run("circle 1in 1in 1in")
        self.backend.run("element* classify")
        ops = self.backend.ops()
        self.assertIsInstance(ops, list)
        self.assertGreater(len(ops), 0)

    def test_help_text(self):
        text = self.backend.help_text("circle")
        self.assertIsInstance(text, str)
        self.assertGreater(len(text), 0)
        self.assertIn("circle", text.lower())


class TestDevice(BackendTestCase):
    def test_default_device_is_dummy(self):
        # The default backend must stay on the dummy driver unchanged.
        self.assertEqual(type(self.backend.device()).__name__, "DummyDevice")

    def test_list_devices_returns_active(self):
        result = device_mod.list_devices(self.backend)
        self.assertIn("devices", result)
        self.assertIn("active", result)
        self.assertIsInstance(result["devices"], list)
        self.assertEqual(result["active"]["label"], "Dummy Device")

    def test_device_status_has_connection_state(self):
        result = device_mod.device_status(self.backend)
        self.assertIn("connected", result)
        self.assertIn("device", result)
        self.assertIn("label", result)
        self.assertIn("position", result)
        self.assertFalse(result["connected"])

    def test_connect_dummy_returns_error_shape(self):
        # The dummy device has no connectable controller, so connect()
        # returns an error shape instead of touching any serial port.
        result = device_mod.connect(self.backend)
        self.assertFalse(result["connected"])
        self.assertIn("error", result)
        self.assertEqual(result["label"], "Dummy Device")

    def test_disconnect_dummy_returns_error_shape(self):
        result = device_mod.disconnect(self.backend)
        self.assertFalse(result["connected"])
        self.assertIn("error", result)


    def test_disconnect_failed_close_preserves_connected_state(self):
        # A failed controller.close() must not report a clean disconnect:
        # the observed live connection state is preserved and the error is
        # attached, so callers do not act on a false disconnected status.
        class _Conn:
            connected = True

        class _Controller:
            connection = _Conn()

            def close(self):
                raise RuntimeError("port busy")

        class _Dev:
            label = "Fake"

            def __init__(self):
                self.controller = _Controller()

        class _Backend:
            def device(self):
                return _Dev()

        result = device_mod.disconnect(_Backend())
        self.assertTrue(result["connected"])
        self.assertIn("error", result)

    def test_active_info_reads_is_connected_for_lihuiyu(self):
        # Lihuiyu controllers expose is_connected() rather than a boolean
        # connected attribute; device status must report the real state.
        class _Conn:
            def is_connected(self):
                return True

        class _Controller:
            connection = _Conn()

        class _Dev:
            label = "Fake"

            def __init__(self):
                self.controller = _Controller()

        info = device_mod._active_info(_Dev())
        self.assertTrue(info["connected"])

class TestDeviceConfig(unittest.TestCase):
    def test_grbl_config_without_opening_serial(self):
        # Starting a grbl backend with a (non-existent) port must set the
        # serial attributes and leave the connection closed. No real serial
        # port is opened.
        backend = Meerk40tBackend(device="grbl", port="/dev/fake", baud=115200)
        try:
            backend.start()
            dev = backend.kernel.device
            self.assertEqual(type(dev).__name__, "GRBLDevice")
            self.assertEqual(dev.serial_port, "/dev/fake")
            self.assertEqual(dev.baud_rate, 115200)
            self.assertFalse(dev.controller.connection.connected)
        finally:
            backend.shutdown()

    def test_backend_serial_config_setter_failure_raises(self):
        # A device whose serial_port setter rejects the value must surface a
        # RuntimeError naming the failing field, not swallow it silently.
        class _FailingSerialDev:
            serial_port = property(
                lambda self: None,
                lambda self, value: (_ for _ in ()).throw(ValueError("rejected")),
            )
            baud_rate = 115200

        backend = Meerk40tBackend(port="/dev/fake", baud=115200)
        with self.assertRaises(RuntimeError) as ctx:
            backend._apply_serial_config(_FailingSerialDev())
        self.assertIn("serial_port", str(ctx.exception))

class TestDeviceProviderAlias(unittest.TestCase):
    """Finding 1: the CLI ``lihuiyu`` choice must resolve to the registered
    provider name ``lhystudios`` when the backend starts a device."""

    def test_lihuiyu_alias_resolves_to_lhystudios(self):
        from cli_anything.meerk40t.utils.meerk40t_backend import (
            _device_provider_name,
        )
        self.assertEqual(_device_provider_name("lihuiyu"), "lhystudios")
        # Every other advertised choice maps 1:1 to its registered provider
        # name (verified against the installed meerk40t package).
        for choice in ("moshi", "ruida", "newly", "balor", "grbl"):
            self.assertEqual(_device_provider_name(choice), choice)

    def test_backend_lihuiyu_starts_lhystudios_device(self):
        # Activating the lhystudios provider headless boots the device service
        # but does NOT open any serial/USB port (the connection is opened
        # lazily via controller.open()), so this is safe without hardware.
        backend = Meerk40tBackend(device="lihuiyu")
        try:
            backend.start()
            dev = backend.kernel.device
            self.assertEqual(type(dev).__name__, "LihuiyuDevice")
        finally:
            backend.shutdown()


class TestDeviceConnectError(unittest.TestCase):
    """Finding 2: a failed open (no hardware) must surface an ``error`` key
    rather than returning a clean status shape."""

    def test_connect_grbl_fake_port_returns_error(self):
        # grbl + a non-existent /dev/fake port: controller.open() swallows the
        # pyserial failure internally and returns with the connection closed.
        backend = Meerk40tBackend(device="grbl", port="/dev/fake", baud=115200)
        try:
            backend.start()
            result = device_mod.connect(backend)
            self.assertFalse(result["connected"])
            self.assertIn("error", result)
            self.assertIn("port=/dev/fake", result["error"])
        finally:
            backend.shutdown()


class TestProject(BackendTestCase):
    def test_create_project(self):
        result = project.create_project(self.backend, name="TestProject")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["name"], "TestProject")
        self.assertIn("elements", result)
        self.assertIn("operations", result)

    def test_open_nonexistent_creates_empty(self):
        path = self.temp_path("does_not_exist.svg")
        result = project.open_project(self.backend, path)
        self.assertEqual(result["path"], path)
        self.assertEqual(result["elements"], 0)

    def test_save_project(self):
        self.backend.run("circle 1in 1in 1in")
        path = self.temp_path("project.svg")
        result = project.save_project(self.backend, path)
        self.assertEqual(result["path"], path)
        self.assertGreater(result["size_bytes"], 0)
        self.assertTrue(os.path.exists(path))

    def test_project_info(self):
        self.backend.run("circle 1in 1in 1in")
        info = project.project_info(self.backend)
        self.assertIn("elements", info)
        self.assertIn("operations", info)
        self.assertIn("device", info)
        self.assertEqual(info["elements"], self.backend.elem_count())


class TestElements(BackendTestCase):
    def test_add_circle(self):
        result = elements.add_circle(self.backend, "1in", "1in", "1in")
        self.assertTrue(result["added"])
        self.assertEqual(result["type"], "circle")
        self.assertGreater(result["total_elements"], 0)

    def test_add_rect_with_stroke_fill(self):
        elements.add_rect(
            self.backend, "1in", "1in", "2in", "2in", stroke="#ff0000", fill="#0000ff"
        )
        rect = None
        for node in self.backend.elems():
            if "rect" in node.type:
                rect = node
                break
        self.assertIsNotNone(rect)
        self.assertEqual(str(rect.stroke), "#ff0000")
        self.assertEqual(str(rect.fill), "#0000ff")

    def test_add_ellipse(self):
        result = elements.add_ellipse(self.backend, "1in", "1in", "0.5in", "0.25in")
        self.assertTrue(result["added"])
        self.assertEqual(result["type"], "ellipse")

    def test_add_line(self):
        result = elements.add_line(
            self.backend, "0in", "0in", "1in", "1in", stroke="#000000"
        )
        self.assertTrue(result["added"])
        self.assertEqual(result["type"], "line")

    def test_add_text(self):
        result = elements.add_text(self.backend, "1in", "1in", "Hello Laser")
        self.assertTrue(result["added"])
        self.assertEqual(result["type"], "text")

    def test_list_elements(self):
        elements.add_circle(self.backend, "1in", "1in", "1in")
        listed = elements.list_elements(self.backend)
        self.assertIsInstance(listed, list)
        self.assertEqual(len(listed), self.backend.elem_count())
        if listed:
            self.assertIn("type", listed[0])
            self.assertIn("stroke", listed[0])
            self.assertIn("fill", listed[0])

    def test_delete_element(self):
        elements.add_circle(self.backend, "1in", "1in", "1in")
        before = self.backend.elem_count()
        result = elements.delete_element(self.backend, 0)
        self.assertTrue(result["deleted"])
        self.assertLess(self.backend.elem_count(), before)

    def test_clear_elements(self):
        elements.add_circle(self.backend, "1in", "1in", "1in")
        elements.add_rect(self.backend, "0in", "0in", "1in", "1in")
        result = elements.clear_elements(self.backend)
        self.assertTrue(result["cleared"])
        self.assertEqual(result["total_elements"], 0)
        self.assertEqual(self.backend.elem_count(), 0)


class TestOperations(BackendTestCase):
    def test_list_operations(self):
        ops = operations.list_operations(self.backend)
        self.assertIsInstance(ops, list)

    def test_add_operation(self):
        before = self.backend.op_count()
        result = operations.add_operation(self.backend, "cut")
        self.assertTrue(result["added"])
        self.assertEqual(result["type"], "cut")
        self.assertGreater(self.backend.op_count(), before)

    def test_classify(self):
        elements.add_circle(self.backend, "1in", "1in", "1in")
        result = operations.classify_elements(self.backend)
        self.assertTrue(result["classified"])
        self.assertGreater(self.backend.op_count(), 0)

    def test_delete_operation(self):
        operations.add_operation(self.backend, "cut")
        before = self.backend.op_count()
        res = operations.delete_operation(self.backend, 0)
        self.assertTrue(res["deleted"])
        self.assertEqual(self.backend.op_count(), before - 1)

    def test_clear_operations(self):
        operations.add_operation(self.backend, "cut")
        operations.add_operation(self.backend, "engrave")
        res = operations.clear_operations(self.backend)
        self.assertTrue(res["cleared"])
        self.assertEqual(self.backend.op_count(), 0)
class TestOperationsValidation(BackendTestCase):
    """Issue #28: reject unsupported operations/properties BEFORE mutation.

    Every failure-path test asserts the prior live state is unchanged.
    """

    # ── unknown operation type fails before console dispatch ─────────────
    def test_add_unknown_type_is_rejected(self):
        before = self.backend.op_count()
        result = operations.add_operation(self.backend, "frobnicate")
        self.assertIn("error", result)
        # Tree must be untouched.
        self.assertEqual(self.backend.op_count(), before)

    def test_add_image_is_rejected_noop(self):
        # backend.run("image") is a silent no-op; shipping it as success is a
        # false positive. It must be rejected, not silently dropped.
        before = self.backend.op_count()
        result = operations.add_operation(self.backend, "image")
        self.assertIn("error", result)
        self.assertEqual(self.backend.op_count(), before)

    # ── delete / set index bounds ──────────────────────────────────────
    def test_set_missing_index_is_rejected(self):
        before = self.backend.op_count()
        result = operations.set_operation(self.backend, before + 5, "power", "500")
        self.assertIn("error", result)
        self.assertEqual(self.backend.op_count(), before)

    def test_delete_missing_index_is_rejected(self):
        before = self.backend.op_count()
        result = operations.delete_operation(self.backend, before + 5)
        self.assertIn("error", result)
        self.assertEqual(self.backend.op_count(), before)

    # ── set: unsupported key, wrong type, non-finite, out-of-range ────
    def test_set_unsupported_key_is_rejected(self):
        operations.add_operation(self.backend, "cut")
        idx = self.backend.op_count() - 1
        before = self.backend.op_count()
        node = self.backend.ops()[idx]
        prev_power = getattr(node, "power", None)
        result = operations.set_operation(self.backend, idx, "not_a_real_key", "1")
        self.assertIn("error", result)
        node2 = self.backend.ops()[idx]
        self.assertEqual(getattr(node2, "power", None), prev_power)
        self.assertEqual(self.backend.op_count(), before)

    def test_set_string_in_number_field_is_rejected(self):
        operations.add_operation(self.backend, "cut")
        idx = self.backend.op_count() - 1
        prev = getattr(self.backend.ops()[idx], "power", None)
        result = operations.set_operation(self.backend, idx, "power", "not-a-number")
        self.assertIn("error", result)
        self.assertEqual(getattr(self.backend.ops()[idx], "power", None), prev)

    def test_set_nonfinite_power_is_rejected(self):
        operations.add_operation(self.backend, "cut")
        idx = self.backend.op_count() - 1
        prev = getattr(self.backend.ops()[idx], "power", None)
        for bad in ("nan", "inf", "-inf", "NaN"):
            result = operations.set_operation(self.backend, idx, "power", bad)
            self.assertIn("error", result, msg=f"value={bad}")
            self.assertEqual(getattr(self.backend.ops()[idx], "power", None), prev)

    def test_set_zero_negative_power_is_rejected(self):
        operations.add_operation(self.backend, "cut")
        idx = self.backend.op_count() - 1
        prev = getattr(self.backend.ops()[idx], "power", None)
        for bad in ("0", "-1", "-5"):
            result = operations.set_operation(self.backend, idx, "power", bad)
            self.assertIn("error", result, msg=f"value={bad}")
            self.assertEqual(getattr(self.backend.ops()[idx], "power", None), prev)

    def test_set_negative_speed_is_rejected(self):
        operations.add_operation(self.backend, "cut")
        idx = self.backend.op_count() - 1
        prev = getattr(self.backend.ops()[idx], "speed", None)
        result = operations.set_operation(self.backend, idx, "speed", "-10")
        self.assertIn("error", result)
        self.assertEqual(getattr(self.backend.ops()[idx], "speed", None), prev)

    def test_set_fractional_passes_is_rejected(self):
        operations.add_operation(self.backend, "cut")
        idx = self.backend.op_count() - 1
        prev = getattr(self.backend.ops()[idx], "passes", None)
        result = operations.set_operation(self.backend, idx, "passes", "2.5")
        self.assertIn("error", result)
        self.assertEqual(getattr(self.backend.ops()[idx], "passes", None), prev)

    def test_set_zero_passes_is_rejected(self):
        operations.add_operation(self.backend, "cut")
        idx = self.backend.op_count() - 1
        prev = getattr(self.backend.ops()[idx], "passes", None)
        result = operations.set_operation(self.backend, idx, "passes", "0")
        self.assertIn("error", result)
        self.assertEqual(getattr(self.backend.ops()[idx], "passes", None), prev)

    # ── success path: requested property read back with validated value ──
    def test_set_power_readback(self):
        operations.add_operation(self.backend, "cut")
        idx = self.backend.op_count() - 1
        result = operations.set_operation(self.backend, idx, "power", "500")
        self.assertNotIn("error", result)
        self.assertEqual(getattr(self.backend.ops()[idx], "power", None), 500.0)

    def test_set_passes_readback(self):
        operations.add_operation(self.backend, "cut")
        idx = self.backend.op_count() - 1
        result = operations.set_operation(self.backend, idx, "passes", "3")
        self.assertNotIn("error", result)
        self.assertEqual(getattr(self.backend.ops()[idx], "passes", None), 3)

    # ── postconditions: count/inventory deltas ────────────────────────
    def test_add_proves_one_op_added(self):
        before = self.backend.op_count()
        result = operations.add_operation(self.backend, "cut")
        self.assertNotIn("error", result)
        self.assertEqual(self.backend.op_count(), before + 1)

    def test_delete_proves_one_op_removed(self):
        operations.add_operation(self.backend, "cut")
        before = self.backend.op_count()
        result = operations.delete_operation(self.backend, before - 1)
        self.assertNotIn("error", result)
        self.assertEqual(self.backend.op_count(), before - 1)

    def test_clear_proves_zero_ops(self):
        operations.add_operation(self.backend, "cut")
        operations.add_operation(self.backend, "engrave")
        result = operations.clear_operations(self.backend)
        self.assertNotIn("error", result)
        self.assertEqual(self.backend.op_count(), 0)

    # ── fresh backend restarts cleanly after rejections ───────────────
    def test_restart_after_rejections(self):
        for bad in ("frobnicate", "image"):
            operations.add_operation(self.backend, bad)
        operations.add_operation(self.backend, "cut")
        self.backend.shutdown()
        self.backend.start()
        self.assertIsNotNone(self.backend.kernel)
        self.assertGreaterEqual(self.backend.op_count(), 0)


class TestSession(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="mk_session_")
        self.session_path = os.path.join(self.temp_dir, "session.json")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_session_save_load(self):
        sess = session.Session(self.session_path)
        sess.name = "LaserJob"
        sess.record_command("circle 1in 1in 1in")
        sess.record_command("rect 0in 0in 1in 1in")
        sess.save()
        self.assertTrue(os.path.exists(self.session_path))

        loaded = session.Session(self.session_path)
        self.assertEqual(loaded.name, "LaserJob")
        self.assertEqual(len(loaded.history), 2)
        self.assertEqual(loaded.history[0]["cmd"], "circle 1in 1in 1in")

    def test_undo_redo(self):
        sess = session.Session(self.session_path)
        sess.record_command("circle 1in 1in 1in")
        sess.record_command("rect 0in 0in 1in 1in")
        self.assertEqual(len(sess.undo_stack), 2)

        undone = sess.undo()
        self.assertEqual(undone, "rect 0in 0in 1in 1in")
        self.assertEqual(len(sess.undo_stack), 1)
        self.assertEqual(len(sess.redo_stack), 1)

        redone = sess.redo()
        self.assertEqual(redone, "rect 0in 0in 1in 1in")
        self.assertEqual(len(sess.undo_stack), 2)
        self.assertEqual(len(sess.redo_stack), 0)


class TestExport(BackendTestCase):
    def test_export_svg(self):
        elements.add_circle(self.backend, "1in", "1in", "1in")
        path = self.temp_path("export.svg")
        result = export.export_svg(self.backend, path)
        self.assertEqual(result["format"], "svg")
        self.assertGreater(result["size_bytes"], 0)
        self.assertTrue(os.path.exists(path))
        root = ET.parse(path).getroot()
        self.assertTrue(root.tag.endswith("svg"))

    def test_export_svgz(self):
        elements.add_circle(self.backend, "1in", "1in", "1in")
        path = self.temp_path("export.svgz")
        result = export.export_svgz(self.backend, path)
        self.assertEqual(result["format"], "svg")
        self.assertGreater(result["size_bytes"], 0)
        self.assertTrue(os.path.exists(path))

    def test_export_png_raises_without_renderer(self):
        elements.add_circle(self.backend, "1in", "1in", "1in")
        with self.assertRaises(RuntimeError) as ctx:
            export.export_png(self.backend, self.temp_path("export.png"))
        self.assertIn("render-op/make_raster", str(ctx.exception))


class TestGeometryTransforms(BackendTestCase):
    def test_translate_element(self):
        from meerk40t.core.units import Length
        elements.add_circle(self.backend, "1in", "1in", "1in")
        res = elements.translate_element(self.backend, 0, "10mm", "20mm")
        self.assertTrue(res["translated"])
        self.assertEqual(res["index"], 0)
        self.assertAlmostEqual(res["after"]["x"] - res["before"]["x"], float(Length("10mm")))
        
    def test_scale_element(self):
        elements.add_circle(self.backend, "1in", "1in", "1in")
        res = elements.scale_element(self.backend, 0, "2.0")
        self.assertTrue(res["scaled"])
        self.assertAlmostEqual(res["after"]["scale_x"], res["before"]["scale_x"] * 2.0)
        
    def test_rotate_element(self):
        elements.add_rect(self.backend, "0in", "0in", "1in", "1in")
        res = elements.rotate_element(self.backend, 0, "90deg")
        self.assertTrue(res["rotated"])
        import math
        self.assertAlmostEqual(abs(res["after_rotation"] - res["before_rotation"]), math.pi / 2, places=3)

    def test_align_elements(self):
        elements.add_circle(self.backend, "0in", "0in", "1in")
        elements.add_circle(self.backend, "2in", "2in", "1in")
        res = elements.align_elements(self.backend, "center", indexes=[0, 1])
        self.assertTrue(res["aligned"])
        self.assertEqual(res["num_elements"], 2)

    def test_group_ungroup(self):
        elements.add_circle(self.backend, "0in", "0in", "1in")
        elements.add_circle(self.backend, "2in", "2in", "1in")
        res = elements.group_elements(self.backend, "MyGroup", indexes=[0, 1])
        self.assertTrue(res["grouped"])
        
        res = elements.ungroup_elements(self.backend)
        self.assertTrue(res["ungrouped"])


class TestREPLDispatch(BackendTestCase):
    def test_dispatch_repl_commands(self):
        from cli_anything.meerk40t.meerk40t_cli import _dispatch_repl
        import click
        from unittest.mock import patch
        
        # Add elements
        elements.add_circle(self.backend, "1in", "1in", "1in")
        
        class DummyContext(click.Context):
            def __init__(self, backend):
                self.obj = {"backend": backend, "session": None}
                
            def exit(self, code=0):
                pass
                
        ctx = DummyContext(self.backend)
        
        # We patch _emit inside meerk40t_cli.py
        with patch("cli_anything.meerk40t.meerk40t_cli._emit") as mock_emit:
            # 1. translate
            _dispatch_repl(ctx, "elements translate 0 10mm 20mm", None, {})
            mock_emit.assert_called_once()
            res = mock_emit.call_args[0][1]
            self.assertTrue(res["translated"])
            self.assertEqual(res["index"], 0)
            mock_emit.reset_mock()
            
            # 2. scale
            _dispatch_repl(ctx, "elements scale 0 2.5", None, {})
            mock_emit.assert_called_once()
            res = mock_emit.call_args[0][1]
            self.assertTrue(res["scaled"])
            self.assertEqual(res["index"], 0)
            mock_emit.reset_mock()
            
            # 3. rotate
            _dispatch_repl(ctx, "elements rotate 0 45deg", None, {})
            mock_emit.assert_called_once()
            res = mock_emit.call_args[0][1]
            self.assertTrue(res["rotated"])
            self.assertEqual(res["index"], 0)
            mock_emit.reset_mock()
            
            # 4. delete operation
            operations.add_operation(self.backend, "cut")
            before_ops = self.backend.op_count()
            _dispatch_repl(ctx, "operations delete 0", None, {})
            mock_emit.assert_called_once()
            res = mock_emit.call_args[0][1]
            self.assertTrue(res["deleted"])
            self.assertEqual(self.backend.op_count(), before_ops - 1)
            mock_emit.reset_mock()


class TestCliDevice(unittest.TestCase):
    """CLI-level wiring tests (catch Click option/decorator regressions)."""

    def _run_json(self, args):
        import io
        import sys
        from click.testing import CliRunner
        from cli_anything.meerk40t import meerk40t_cli

        capture = io.StringIO()
        orig = meerk40t_cli._REAL_STDOUT
        meerk40t_cli._REAL_STDOUT = capture
        try:
            runner = CliRunner()
            result = runner.invoke(meerk40t_cli.cli, ["--json"] + args)
        finally:
            meerk40t_cli._REAL_STDOUT = orig
            sys.stdout = orig
        return result, capture.getvalue()

    def test_cli_grbl_status_wiring(self):
        result, out = self._run_json(
            ["--device", "grbl", "--port", "/dev/fake", "--baud", "115200", "device", "status"]
        )
        self.assertEqual(result.exit_code, 0, result.output)
        data = json.loads(out)
        self.assertEqual(data["type"], "GRBLDevice")
        self.assertEqual(data["port"], "/dev/fake")
        self.assertEqual(data["baud"], 115200)
        # No serial port is opened by device status.
        self.assertFalse(data["connected"])

    def test_cli_dummy_connect_error_wiring(self):
        # A failed connect is a structured failure: it must exit nonzero while
        # still emitting a payload that explains why (connected=False + error).
        result, out = self._run_json(["device", "connect"])
        self.assertEqual(result.exit_code, 1, result.output)
        data = json.loads(out)
        self.assertFalse(data["connected"])
        self.assertIn("error", data)

    def test_cli_help_lists_device_options(self):
        from click.testing import CliRunner
        from cli_anything.meerk40t import meerk40t_cli

        runner = CliRunner()
        result = runner.invoke(meerk40t_cli.cli, ["--help"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--device", result.output)
        self.assertIn("--port", result.output)
        self.assertIn("--baud", result.output)


class TestGrblParsers(unittest.TestCase):
    """Pure parser tests on canned GRBL 1.1 output (no hardware opened)."""

    def test_parse_settings_canned(self):
        text = "$0=10\n$1=25\n$32=1\n$130=410.000\n$131=400.000"
        settings = device_mod.parse_settings(text)
        self.assertEqual(settings[0], "10")
        self.assertEqual(settings[32], "1")
        self.assertEqual(settings[130], "410.000")
        self.assertIsInstance(next(iter(settings)), int)

    def test_parse_startup_blocks_canned(self):
        text = "$N0=\n$N1=G91 G21"
        blocks = device_mod.parse_startup_blocks(text)["startup_blocks"]
        self.assertEqual(
            blocks,
            [
                {"index": 0, "block": ""},
                {"index": 1, "block": "G91 G21"},
            ],
        )

    def test_parse_grbl_probe_banner(self):
        ident = serial_probe.parse_grbl_probe("Grbl 1.1f ['$' for help]")
        self.assertEqual(ident["firmware"], "Grbl")
        self.assertEqual(ident["version"], "1.1f")
        self.assertIsNone(ident["state"])

    def test_parse_grbl_probe_ver_and_state(self):
        ident = serial_probe.parse_grbl_probe(
            "[VER:1.1f.20170801:]\n<Idle|WPos:0.000,0.000,0.000>"
        )
        self.assertEqual(ident["version"], "1.1f.20170801")
        self.assertEqual(ident["state"], "Idle")

    def test_parse_grbl_probe_empty(self):
        ident = serial_probe.parse_grbl_probe("")
        self.assertIsNone(ident["firmware"])
        self.assertIsNone(ident["version"])
        self.assertIsNone(ident["state"])


class TestJogRefusalWithoutConnection(BackendTestCase):
    """jog/goto/frame must refuse without a live connection."""

    def test_jog_refused(self):
        res = device_mod.jog(self.backend, 10.0, 10.0, feed=600)
        self.assertFalse(res["connected"])
        self.assertIn("error", res)
        self.assertEqual(res["command"], "jog")

    def test_goto_refused(self):
        res = device_mod.goto(self.backend, 5.0, 5.0, feed=3000)
        self.assertFalse(res["connected"])
        self.assertIn("error", res)
        self.assertEqual(res["command"], "goto")

    def test_frame_refused(self):
        res = device_mod.frame(self.backend, 0.0, 0.0, 10.0, 10.0, feed=1500)
        self.assertFalse(res["connected"])
        self.assertIn("error", res)
        self.assertEqual(res["command"], "frame")


class TestFrameCornerMath(unittest.TestCase):
    """frame traces the rectangle corners in machine-mm order."""

    def _live_backend(self):
        class _Conn:
            connected = True

        class _Channel:
            def __init__(self):
                self._watchers = []

            def watch(self, cb):
                self._watchers.append(cb)

            def unwatch(self, cb):
                if cb in self._watchers:
                    self._watchers.remove(cb)

            def push(self, payload):
                for cb in list(self._watchers):
                    cb(payload)

        class _Controller:
            def __init__(self, channel):
                self.connection = _Conn()
                self.written = []
                self._channel = channel

            def write(self, line):
                self.written.append(line)
                self._channel.push("ok")

        class _Dev:
            label = "Fake"
            safe_label = "Fake"

            def __init__(self, controller):
                self.controller = controller

            def __str__(self):
                return "FakeDevice"

        channel = _Channel()
        controller = _Controller(channel)
        dev = _Dev(controller)
        backend = type("Backend", (), {})()
        backend.device = lambda: dev
        backend.kernel = type(
            "Kernel", (), {"channel": staticmethod(lambda name: channel)}
        )()
        return backend, controller

    def test_frame_traces_five_corners(self):
        backend, controller = self._live_backend()
        res = device_mod.frame(backend, 10.0, 20.0, 30.0, 40.0, feed=1500)
        self.assertTrue(res["framed"])
        expected = [
            (10.0, 20.0),
            (40.0, 20.0),
            (40.0, 60.0),
            (10.0, 60.0),
            (10.0, 20.0),
        ]
        got = [(c["x"], c["y"]) for c in res["corners"]]
        self.assertEqual(got, expected)
        self.assertEqual(len(controller.written), 5)
        for line in controller.written:
            self.assertTrue(line.startswith("$J=G53G21G90 "))
            self.assertIn("F1500", line)


class TestJogExactStrings(unittest.TestCase):
    """P1/P2/P3: jog/goto/frame emit the exact GRBL 1.1 jog words and report
    GRBL acknowledgement (ok/error) instead of assuming success."""

    def _backend(self, reply="ok", connected=True):
        class _Conn:
            connected = True

        class _Channel:
            def __init__(self):
                self._watchers = []

            def watch(self, cb):
                self._watchers.append(cb)

            def unwatch(self, cb):
                if cb in self._watchers:
                    self._watchers.remove(cb)

            def push(self, payload):
                for cb in list(self._watchers):
                    cb(payload)

        class _Controller:
            def __init__(self, channel):
                self.connection = _Conn() if connected else None
                self.written = []
                self._channel = channel

            def write(self, line):
                self.written.append(line)
                if connected and self._channel is not None and reply:
                    self._channel.push(reply)

        class _Dev:
            safe_label = "FakeGRBL"
            controller = None

            def __init__(self, controller):
                if connected:
                    self.controller = controller

            def __str__(self):
                return "GRBLDevice"

        channel = _Channel()
        controller = _Controller(channel)
        dev = _Dev(controller)
        backend = type("Backend", (), {})()
        backend.device = lambda: dev
        backend.kernel = type(
            "Kernel", (), {"channel": staticmethod(lambda name: channel)}
        )()
        return backend, controller

    def test_jog_emits_relative_word(self):
        backend, controller = self._backend()
        res = device_mod.jog(backend, 10.0, 10.0, feed=600)
        self.assertTrue(res["jogged"])
        self.assertEqual(controller.written[0], "$J=G21G91 X10.0 Y10.0 F600\n")
        self.assertEqual(res["command"], "$J=G21G91 X10.0 Y10.0 F600")
        self.assertTrue(res["acknowledged"])
        self.assertEqual(res["response"], "ok")
        self.assertIsNone(res["error"])

    def test_goto_emits_absolute_word(self):
        backend, controller = self._backend()
        res = device_mod.goto(backend, 0.0, 0.0, feed=3000)
        self.assertTrue(res["jogged"])
        self.assertEqual(controller.written[0], "$J=G53G21G90 X0.0 Y0.0 F3000\n")
        self.assertEqual(res["command"], "$J=G53G21G90 X0.0 Y0.0 F3000")
        self.assertTrue(res["acknowledged"])

    def test_frame_emits_absolute_words(self):
        backend, controller = self._backend()
        res = device_mod.frame(backend, 10.0, 20.0, 30.0, 40.0, feed=1500)
        self.assertTrue(res["framed"])
        self.assertEqual(len(controller.written), 5)
        for line in controller.written:
            self.assertTrue(line.startswith("$J=G53G21G90 "))
            self.assertIn("F1500", line)

    def test_jog_reports_error_response(self):
        backend, controller = self._backend(reply="error:9")
        res = device_mod.jog(backend, 10.0, 10.0, feed=600)
        self.assertFalse(res["acknowledged"])
        self.assertEqual(res["error"], "error:9")
        self.assertEqual(res["response"], "error:9")

    def test_jog_unacknowledged_without_reply(self):
        # No recv reply (e.g. no hardware): must not claim success.
        backend, controller = self._backend(reply=None)
        res = device_mod.jog(backend, 10.0, 10.0, feed=600)
        self.assertFalse(res["acknowledged"])
        self.assertIsNone(res["response"])


class TestProfileOverlay(unittest.TestCase):
    """User profiles win over bundled ones; unknown names resolve to None."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk_prof_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_user(self, name, payload):
        d = os.path.join(self.tmp, "profiles")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{name}.json"), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def test_user_overrides_bundled(self):
        self._write_user(
            "sculpfun-s9",
            {"name": "sculpfun-s9", "device": "grbl", "baud": 57600},
        )
        prof = profiles_mod.load_profile("sculpfun-s9", config_home=self.tmp)
        self.assertEqual(prof["baud"], 57600)
        origins = {p["name"]: p["origin"] for p in profiles_mod.list_profiles(self.tmp)}
        self.assertEqual(origins["sculpfun-s9"], "user")

    def test_user_only_profile(self):
        self._write_user("my-creality", {"name": "my-creality", "device": "grbl"})
        prof = profiles_mod.load_profile("my-creality", config_home=self.tmp)
        self.assertEqual(prof["name"], "my-creality")

    def test_unknown_profile_is_none(self):
        self.assertIsNone(profiles_mod.load_profile("ghost", config_home=self.tmp))

    def test_available_names_includes_both(self):
        self._write_user("my-creality", {"name": "my-creality", "device": "grbl"})
        names = profiles_mod.available_names(self.tmp)
        self.assertIn("sculpfun-s9", names)
        self.assertIn("my-creality", names)


class TestSetupProfileWrites(unittest.TestCase):
    """setup_profile writes a correct JSON profile from injected readback."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk_setup_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _grbl_backend(self):
        class _Conn:
            connected = False

        class _Controller:
            def __init__(self):
                self.connection = _Conn()

            def open(self):
                self.connection.connected = True

            def write(self, line):
                pass

            def close(self):
                self.connection.connected = False

        class _Dev:
            label = "GRBL"
            baud_rate = 115200

            def __init__(self):
                self.controller = _Controller()

            def __str__(self):
                return "GRBLDevice"

        dev = _Dev()
        backend = type("Backend", (), {})()
        backend.device = lambda: dev
        return backend

    def test_setup_writes_correct_json(self):
        backend = self._grbl_backend()
        settings_text = "$32=1\n$130=300.000\n$131=200.000"
        ident_text = "$I\n[VER:1.1f:]\n"
        res = device_mod.setup_profile(
            backend,
            "myprofile",
            settings_text=settings_text,
            ident_text=ident_text,
            config_home=self.tmp,
        )
        self.assertTrue(res["saved"])
        path = os.path.join(self.tmp, "profiles", "myprofile.json")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["name"], "myprofile")
        self.assertEqual(data["device"], "grbl")
        self.assertEqual(data["baud"], 115200)
        self.assertEqual(data["bedwidth"], "300.000mm")
        self.assertEqual(data["bedheight"], "200.000mm")
        self.assertEqual(data["provenance"]["firmware"], "Grbl 1.1f")
        self.assertTrue(data["provenance"]["verified"])
    def test_setup_unverified_when_no_readback(self):
        backend = self._grbl_backend()
        res = device_mod.setup_profile(
            backend,
            "empty",
            settings_text="",
            ident_text="",
            config_home=self.tmp,
        )
        self.assertTrue(res["saved"])
        provenance = res["profile"]["provenance"]
        self.assertFalse(provenance["verified"])
        self.assertIsNone(provenance["firmware"])
    def test_setup_writes_via_config_home_env(self):
        # Contract item 2: profile written under a tmpdir pointed to by
        # CLI_ANYTHING_CONFIG_HOME, with no config_home argument passed.
        old = os.environ.get("CLI_ANYTHING_CONFIG_HOME")
        os.environ["CLI_ANYTHING_CONFIG_HOME"] = self.tmp
        try:
            backend = self._grbl_backend()
            res = device_mod.setup_profile(
                backend,
                "envprofile",
                settings_text="$32=1\n$130=410.000\n$131=400.000",
                ident_text="$I\n[VER:1.1f:]\n",
            )
        finally:
            if old is None:
                os.environ.pop("CLI_ANYTHING_CONFIG_HOME", None)
            else:
                os.environ["CLI_ANYTHING_CONFIG_HOME"] = old
        self.assertTrue(res["saved"])
        path = os.path.join(self.tmp, "profiles", "envprofile.json")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data["name"], "envprofile")
        self.assertEqual(data["bedwidth"], "410.000mm")
        self.assertEqual(data["provenance"]["firmware"], "Grbl 1.1f")



class TestExportGuard(unittest.TestCase):
    """G-code export refuses default-power ops; placement summary parses."""

    def test_export_gcode_refuses_default_power(self):
        backend = Meerk40tBackend()
        backend.start()
        try:
            operations.add_operation(backend, "cut")
            path = os.path.join(tempfile.mkdtemp(prefix="mk_exp_"), "job.gcode")
            res = export.export_gcode(backend, path)
        finally:
            backend.shutdown()
        self.assertIn("error", res)
        self.assertIn("default_power_ops", res)
        self.assertFalse(res["pass"])

    def test_parse_placement_summary(self):
        gcode = (
            "G0 X10 Y20\n"
            "G1 X50 Y60 S500 F1000\n"
            "G1 X10 Y20 S500 F1000\n"
        )
        summary = export.parse_placement_summary(gcode, "410mm", "400mm")
        self.assertEqual(summary["x_range"], [10.0, 50.0])
        self.assertEqual(summary["y_range"], [20.0, 60.0])
        self.assertEqual(summary["bed"], {"w": 410.0, "h": 400.0})
        self.assertEqual(summary["s_values"], [500])
        self.assertEqual(summary["feeds"], [1000])


class TestMachineBedApplication(unittest.TestCase):
    """P1 #1: a --machine profile bed must reach the device view and thus the
    export placement summary."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk_bed_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_apply_machine_profile_reaches_export_placement(self):
        backend = Meerk40tBackend(device="grbl", port=None, baud=115200)
        backend.start()
        try:
            cli_mod._apply_machine_profile(
                backend, {"bedwidth": "410mm", "bedheight": "400mm"}
            )
            dev = backend.device()
            self.assertEqual(getattr(dev, "bedwidth", None), "410mm")
            self.assertEqual(getattr(dev, "bedheight", None), "400mm")
            operations.add_operation(backend, "cut")
            elements.add_rect(backend, 0, 0, 10, 10)
            path = os.path.join(self.tmp, "job.gcode")
            res = export.export_gcode(backend, path, allow_full_power=True)
            self.assertNotIn("error", res, res)
            self.assertEqual(res["placement"]["bed"], {"w": 410.0, "h": 400.0})
        finally:
            backend.shutdown()


class TestCliMachineProfile(unittest.TestCase):
    """CLI-level machine-profile wiring (unknown name -> JSON error, exit 1)."""

    def _run_json(self, args):
        import io
        import sys
        from click.testing import CliRunner
        from cli_anything.meerk40t import meerk40t_cli

        capture = io.StringIO()
        orig = meerk40t_cli._REAL_STDOUT
        meerk40t_cli._REAL_STDOUT = capture
        try:
            runner = CliRunner()
            result = runner.invoke(meerk40t_cli.cli, ["--json"] + args)
        finally:
            meerk40t_cli._REAL_STDOUT = orig
            sys.stdout = orig
        return result, capture.getvalue()

    def test_cli_unknown_machine_error(self):
        result, out = self._run_json(["--machine", "ghost", "device", "detect"])
        self.assertEqual(result.exit_code, 1, result.output)
        data = json.loads(out)
        self.assertIn("error", data)
        self.assertIn("unknown machine profile", data["error"])
        self.assertIn("sculpfun-s9", data["known"])

    def test_cli_machine_list_bundled(self):
        result, out = self._run_json(["machine", "list"])
        self.assertEqual(result.exit_code, 0, result.output)
        data = json.loads(out)
        names = [p["name"] for p in data["profiles"]]
        self.assertIn("sculpfun-s9", names)
        origins = {p["name"]: p["origin"] for p in data["profiles"]}
        self.assertEqual(origins["sculpfun-s9"], "bundled")

    def test_cli_offline_command_works_with_machine_alone(self):
        # P2 #4: offline commands work with --machine and no --port.
        result, out = self._run_json(["--machine", "sculpfun-s9", "machine", "list"])
        self.assertEqual(result.exit_code, 0, result.output)
        data = json.loads(out)
        names = [p["name"] for p in data["profiles"]]
        self.assertIn("sculpfun-s9", names)

    def test_cli_serial_command_no_port_gate_defers_to_connect(self):
        # P2 #4: a serial command without --port is no longer blocked by a
        # global gate; it reaches the device layer and reports the real
        # connection error instead of "--machine requires --port".
        result, out = self._run_json(
            ["--machine", "sculpfun-s9", "device", "jog", "10", "10"]
        )
        data = json.loads(out)
class TestProfileSubmitUnit(unittest.TestCase):
    """Unit coverage for the community submission module."""

    def setUp(self):
        self.good = profiles_mod.load_profile("sculpfun-s9")
        self.assertIsNotNone(self.good)

    def test_validate_accepts_valid_profile(self):
        self.assertEqual(submit_mod.validate_submission(self.good), [])

    def test_validate_rejects_missing_key(self):
        bad = dict(self.good)
        del bad["baud"]
        errs = submit_mod.validate_submission(bad)
        self.assertTrue(any("missing required key: baud" in e for e in errs))

    def test_validate_rejects_wrong_type(self):
        bad = dict(self.good)
        bad["has_endstops"] = 0  # int where bool expected
        errs = submit_mod.validate_submission(bad)
        self.assertTrue(any("must be a bool" in e for e in errs))

    def test_validate_rejects_bool_for_int(self):
        bad = dict(self.good)
        bad["baud"] = True  # bool where int expected
        errs = submit_mod.validate_submission(bad)
        self.assertTrue(any("must be an int (got bool)" in e for e in errs))

    def test_validate_rejects_empty_provenance(self):
        bad = dict(self.good)
        bad["provenance"] = {}
        errs = submit_mod.validate_submission(bad)
        self.assertTrue(any("provenance" in e for e in errs))

    def test_issue_url_is_encoded(self):
        url = submit_mod.build_issue_url(self.good)
        self.assertTrue(
            url.startswith(
                "https://github.com/George-RD/cli-anything-meerk40t/issues/new?"
            )
        )
        self.assertIn("labels=community-profile", url)
        self.assertNotIn(" ", url)  # fully URL-encoded, no raw spaces
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(qs["labels"], ["community-profile"])
        title = urllib.parse.unquote(qs["title"][0])
        self.assertIn("sculpfun-s9", title)
        body = urllib.parse.unquote(qs["body"][0])
        self.assertIn("grbl", body)
        self.assertIn("```json", body)

    def test_gh_installed_detection_true(self):
        with unittest.mock.patch(
            "cli_anything.meerk40t.utils.submit.shutil.which",
            return_value="/usr/bin/gh",
        ):
            self.assertTrue(submit_mod.gh_installed())

    def test_gh_installed_detection_false(self):
        with unittest.mock.patch(
            "cli_anything.meerk40t.utils.submit.shutil.which",
            return_value=None,
        ):
            self.assertFalse(submit_mod.gh_installed())

    def test_plan_without_yes_has_no_side_effects(self):
        with unittest.mock.patch(
            "cli_anything.meerk40t.utils.submit.subprocess.run"
        ) as mock_run:
            res = submit_mod.submit_profile("sculpfun-s9", yes=False)
        self.assertTrue(res["ok"])
        self.assertIs(res["submitted"], False)
        self.assertEqual(
            res["community_file"], "profiles/community/sculpfun-s9.json"
        )
        self.assertIn("issue_url", res)
        mock_run.assert_not_called()

    def test_yes_without_gh_falls_back_to_issue_url(self):
        with unittest.mock.patch(
            "cli_anything.meerk40t.utils.submit.shutil.which",
            return_value=None,
        ):
            with unittest.mock.patch(
                "cli_anything.meerk40t.utils.submit.subprocess.run"
            ) as mock_run:
                res = submit_mod.submit_profile("sculpfun-s9", yes=True)
        self.assertIs(res["submitted"], False)
        self.assertEqual(res["method"], "issue-url")
        mock_run.assert_not_called()

    def test_yes_with_gh_opens_pull_request(self):
        tmp = tempfile.mkdtemp(prefix="mk_sub_")
        try:
            fake_pr = "https://github.com/George-RD/cli-anything-meerk40t/pull/1"
            with unittest.mock.patch(
                "cli_anything.meerk40t.utils.submit.shutil.which",
                return_value="/usr/bin/gh",
            ):
                with unittest.mock.patch(
                    "cli_anything.meerk40t.utils.submit.os.getcwd",
                    return_value=tmp,
                ):
                    with unittest.mock.patch(
                        "cli_anything.meerk40t.utils.submit.subprocess.run",
                        return_value=subprocess.CompletedProcess(
                            args=[], returncode=0, stdout=fake_pr
                        ),
                    ) as mock_run:
                        res = submit_mod.submit_profile("sculpfun-s9", yes=True)
            self.assertIs(res["submitted"], True)
            self.assertEqual(res["method"], "pull-request")
            self.assertEqual(res["pr_url"], fake_pr)
            # git/gh were driven, including the `gh pr create` step.
            commands = [c.args[0] for c in mock_run.call_args_list]
            self.assertTrue(any(c[:2] == ["gh", "pr"] for c in commands))
            self.assertTrue(any(c[:2] == ["git", "push"] for c in commands))
            # The profile file was staged in the (temp) repo root.
            staged = os.path.join(tmp, "profiles", "community", "sculpfun-s9.json")
            self.assertTrue(os.path.exists(staged))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestProfileSubmitCli(unittest.TestCase):
    """CLI-level wiring for `profile submit` (plan path, no side effects)."""

    def _run_json(self, args):
        import io
        import sys
        from click.testing import CliRunner

        capture = io.StringIO()
        orig = cli_mod._REAL_STDOUT
        cli_mod._REAL_STDOUT = capture
        try:
            runner = CliRunner()
            result = runner.invoke(cli_mod.cli, ["--json"] + args)
        finally:
            cli_mod._REAL_STDOUT = orig
            sys.stdout = orig
        return result, capture.getvalue()

    def test_cli_submit_plan_without_yes(self):
        result, out = self._run_json(["profile", "submit", "sculpfun-s9"])
        self.assertEqual(result.exit_code, 0, result.output)
        data = json.loads(out)
        self.assertTrue(data["ok"])
        self.assertIs(data["submitted"], False)
        self.assertEqual(
            data["community_file"], "profiles/community/sculpfun-s9.json"
        )
        self.assertIn("issue_url", data)
        # No file was written to the working tree.
        self.assertFalse(
            os.path.exists("profiles/community/sculpfun-s9.json")
        )

    def test_cli_submit_unknown_profile_errors(self):
        result, out = self._run_json(["profile", "submit", "ghost-machine"])
        self.assertEqual(result.exit_code, 1, result.output)
        data = json.loads(out)
        self.assertIn("error", data)
        self.assertIn("unknown profile", data["error"])

    def test_cli_submit_invalid_profile_errors(self):
        tmp = tempfile.mkdtemp(prefix="mk_subinv_")
        try:
            bad_path = os.path.join(tmp, "profiles", "broken.json")
            os.makedirs(os.path.dirname(bad_path), exist_ok=True)
            with open(bad_path, "w", encoding="utf-8") as fh:
                json.dump(
                    {"name": "broken", "device": "grbl", "baud": "fast"},
                    fh,
                )
            with unittest.mock.patch.dict(
                os.environ, {"CLI_ANYTHING_CONFIG_HOME": tmp}
            ):
                result, out = self._run_json(
                    ["profile", "submit", "broken"]
                )
            self.assertEqual(result.exit_code, 1, result.output)
            data = json.loads(out)
            self.assertIn("validation_errors", data)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertIn("error", data)
        self.assertNotIn("--machine requires --port", out)



class TestSkillPackaging(unittest.TestCase):
    """The packaged skill copy must ship the router and every reference it
    links, byte-identical to the canonical skills/ tree."""

    def _roots(self):
        import cli_anything.meerk40t as pkg

        pkg_root = os.path.join(os.path.dirname(pkg.__file__), "skills")
        repo_root = os.path.join(
            os.path.dirname(pkg.__file__), "..", "..",
            "skills", "cli-anything-meerk40t",
        )
        return os.path.abspath(repo_root), pkg_root

    def test_packaged_router_matches_canonical(self):
        repo_root, pkg_root = self._roots()
        if not os.path.isdir(repo_root):
            self.skipTest("canonical skills/ tree not present (installed wheel)")
        for rel in ["SKILL.md"]:
            with open(os.path.join(repo_root, rel), "rb") as a, open(
                os.path.join(pkg_root, rel), "rb"
            ) as b:
                self.assertEqual(a.read(), b.read(), rel)

    def test_every_linked_reference_is_packaged(self):
        import re

        _, pkg_root = self._roots()
        router = open(os.path.join(pkg_root, "SKILL.md")).read()
        refs = set(re.findall(r"\]\((references/[a-z-]+\.md)\)", router))
        self.assertTrue(refs, "router links no references")
        for rel in sorted(refs):
            path = os.path.join(pkg_root, rel)
            self.assertTrue(os.path.isfile(path), f"missing packaged {rel}")
            repo_root, _ = self._roots()
            canonical = os.path.join(repo_root, rel)
            if os.path.isfile(canonical):
                with open(canonical, "rb") as a, open(path, "rb") as b:
                    self.assertEqual(a.read(), b.read(), rel)


# ── Smart laser workflow unit tests (plan Step 9) ───────────────────────────

from cli_anything.meerk40t.utils import materials as materials_mod
from cli_anything.meerk40t.utils import job_prep as job_prep_mod
from cli_anything.meerk40t.utils.attach_client import (
    send as attach_send,
    AttachError,
    FRAME_PREFIX,
)
import socketserver
import threading


class JobFixtureTestCase(unittest.TestCase):
    """Helpers: a tiny red SVG + a cut-only estimated material fixture (tmp home)."""

    def _make_red_svg(self, path: str) -> None:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="50mm" '
            'viewBox="0 0 50 50">\n'
            '  <rect x="5" y="5" width="30" height="30" '
            'stroke="#ff0000" fill="none" stroke-width="1"/>\n'
            '</svg>\n'
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)

    def _make_cut_material(self, name: str, config_home: str) -> None:
        data = {
            "name": name,
            "description": "fixture material (cut only, estimated)",
            "machines": {
                "sculpfun-s9": {
                    "roles": {
                        "cut": {
                            "kind": "cut",
                            "passes": 1,
                            "power": 650,
                            "speed": 16.0,
                            "provenance": "estimated",
                            "note": "fixture estimate",
                        }
                    }
                }
            },
        }
        materials_mod.save_user_material(name, data, config_home=config_home)


class TestMaterialsLoader(unittest.TestCase):
    """Bundled materials load; user overrides win; unknown machine rejected."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk_mat_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bundled_kraft_loads(self):
        mat = materials_mod.load_material("kraft-350gsm")
        self.assertIsNotNone(mat)
        self.assertEqual(mat["name"], "kraft-350gsm")
        roles = materials_mod.resolve_settings(mat, "sculpfun-s9")
        self.assertIn("cut", roles)
        self.assertIn("score", roles)
        self.assertIn("etch", roles)
        # Only score is operator-confirmed tested in the bundled record.
        self.assertEqual(roles["score"]["provenance"], "tested")

    def test_user_override_wins(self):
        override = {
            "name": "kraft-350gsm",
            "description": "user override",
            "machines": {
                "sculpfun-s9": {
                    "roles": {
                        "cut": {
                            "kind": "cut",
                            "passes": 1,
                            "power": 999,
                            "speed": 10.0,
                            "provenance": "estimated",
                            "note": "override",
                        }
                    }
                }
            },
        }
        materials_mod.save_user_material("kraft-350gsm", override, config_home=self.tmp)
        mat = materials_mod.load_material("kraft-350gsm", config_home=self.tmp)
        self.assertEqual(mat["description"], "user override")
        roles = materials_mod.resolve_settings(mat, "sculpfun-s9")
        self.assertEqual(roles["cut"]["power"], 999)

    def test_resolve_settings_unknown_machine_raises(self):
        mat = materials_mod.load_material("kraft-350gsm")
        with self.assertRaises(ValueError):
            materials_mod.resolve_settings(mat, "ghost-machine")


class TestJobPrepProvenance(JobFixtureTestCase):
    """The provenance gate blocks estimated settings until acknowledged."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk_prep_")
        self.out = tempfile.mkdtemp(prefix="mk_out_")
        self.mat_name = "cut-est-fixture"
        self._make_cut_material(self.mat_name, self.tmp)
        self.svg = os.path.join(self.tmp, "design.svg")
        self._make_red_svg(self.svg)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.out, ignore_errors=True)

    def test_estimated_cut_raises_without_allow(self):
        # The gate fires before the kernel starts, so no backend boot happens.
        with self.assertRaises(job_prep_mod.UncalibratedSettingsError) as ctx:
            job_prep_mod.prepare_job(
                self.svg,
                self.out,
                machine="sculpfun-s9",
                material=self.mat_name,
                color_map={"#ff0000": "cut"},
                allow_estimated=False,
                config_home=self.tmp,
            )
        self.assertIn("cut", ctx.exception.estimated_roles)

    def test_estimated_cut_passes_with_allow(self):
        summary = job_prep_mod.prepare_job(
            self.svg,
            self.out,
            machine="sculpfun-s9",
            material=self.mat_name,
            color_map={"#ff0000": "cut"},
            allow_estimated=True,
            config_home=self.tmp,
        )
        self.assertEqual(summary["estimated_roles"], ["cut"])
        self.assertTrue(os.path.exists(summary["manifest"]))


class TestJobManifest(JobFixtureTestCase):
    """prepare_job writes a verifiable manifest; preflight rejects tampering."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk_man_")
        self.out = tempfile.mkdtemp(prefix="mk_manout_")
        self.mat_name = "cut-est-fixture"
        self._make_cut_material(self.mat_name, self.tmp)
        self.svg = os.path.join(self.tmp, "design.svg")
        self._make_red_svg(self.svg)
        self.summary = job_prep_mod.prepare_job(
            self.svg,
            self.out,
            machine="sculpfun-s9",
            material=self.mat_name,
            color_map={"#ff0000": "cut"},
            allow_estimated=True,
            config_home=self.tmp,
        )
        self.manifest_path = self.summary["manifest"]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.out, ignore_errors=True)

    def test_manifest_written_and_hashes_verify(self):
        with open(self.manifest_path, encoding="utf-8") as fh:
            manifest = json.loads(fh.read())
        self.assertEqual(manifest["schema"], "clia-job-manifest-v1")
        for fname in ("input_svg", "job_svg", "gcode"):
            entry = manifest["files"][fname]
            apath = os.path.normpath(
                os.path.join(os.path.dirname(self.manifest_path), entry["path"])
            )
            actual = cli_mod._sha256_file(apath)
            self.assertEqual(actual, entry["sha256"], fname)

    def test_preflight_rejects_tampered_gcode(self):
        # Point preflight's material lookup at our tmp config home.
        prev = os.environ.get("CLI_ANYTHING_CONFIG_HOME")
        os.environ["CLI_ANYTHING_CONFIG_HOME"] = self.tmp
        try:
            with open(self.manifest_path, encoding="utf-8") as fh:
                gcode_rel = json.loads(fh.read())["files"]["gcode"]["path"]
            gcode_path = os.path.normpath(
                os.path.join(os.path.dirname(self.manifest_path), gcode_rel)
            )
            with open(gcode_path, "a", encoding="utf-8") as fh:
                fh.write("; tampered\n")
            result, code = cli_mod._run_preflight(
                self.manifest_path, allow_estimated=True
            )
        finally:
            if prev is None:
                os.environ.pop("CLI_ANYTHING_CONFIG_HOME", None)
            else:
                os.environ["CLI_ANYTHING_CONFIG_HOME"] = prev
        self.assertFalse(result["ok"])
        self.assertNotEqual(code, 0)
        self.assertTrue(
            any("gcode hash mismatch" in f for f in result["failures"]),
            result["failures"],
        )


class TestJobPrepValidation(JobFixtureTestCase):
    """Findings 5, 8, 9: value/geometry validation and custom-map role filtering."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk_jpv_")
        self.out = tempfile.mkdtemp(prefix="mk_jpvout_")
        self.svg = os.path.join(self.tmp, "design.svg")
        self._make_red_svg(self.svg)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.out, ignore_errors=True)

    def _make_3role_material(self, name, config_home, *, cut_provenance="estimated"):
        data = {
            "name": name,
            "description": "fixture 3-role material",
            "machines": {
                "sculpfun-s9": {
                    "roles": {
                        "etch": {
                            "kind": "engrave", "passes": 1, "power": 300,
                            "speed": 40.0, "provenance": "tested", "note": "f",
                        },
                        "score": {
                            "kind": "engrave", "passes": 1, "power": 250,
                            "speed": 30.0, "provenance": "tested", "note": "f",
                        },
                        "cut": {
                            "kind": "cut", "passes": 1, "power": 650,
                            "speed": 16.0, "provenance": cut_provenance, "note": "f",
                        },
                    }
                }
            },
        }
        materials_mod.save_user_material(name, data, config_home=config_home)

    def _make_value_material(self, name, config_home, *, power=650, speed=16.0, passes=1):
        data = {
            "name": name,
            "description": "fixture value material",
            "machines": {
                "sculpfun-s9": {
                    "roles": {
                        "cut": {
                            "kind": "cut", "passes": passes, "power": power,
                            "speed": speed, "provenance": "tested", "note": "f",
                        }
                    }
                }
            },
        }
        materials_mod.save_user_material(name, data, config_home=config_home)

    def test_custom_map_single_role_no_keyerror(self):
        # Finding 9: a three-role material with a single-role custom map must
        # produce exactly one op (cut) and limit estimated_roles to that role.
        name = "three-role-fixture"
        self._make_3role_material(name, self.tmp, cut_provenance="estimated")
        summary = job_prep_mod.prepare_job(
            self.svg, self.out,
            machine="sculpfun-s9", material=name,
            color_map={"#ff0000": "cut"}, allow_estimated=True,
            config_home=self.tmp,
        )
        self.assertEqual(len(summary["operations"]), 1)
        self.assertEqual(summary["operations"][0]["kind"], "cut")
        self.assertEqual(summary["estimated_roles"], ["cut"])

    def test_custom_map_missing_role_raises(self):
        # Finding 9: a role named in the map that the material lacks still
        # raises MissingRoleError.
        name = "three-role-fixture"
        self._make_3role_material(name, self.tmp)
        with self.assertRaises(job_prep_mod.MissingRoleError):
            job_prep_mod.prepare_job(
                self.svg, self.out,
                machine="sculpfun-s9", material=name,
                color_map={"#ff0000": "engrave"}, allow_estimated=True,
                config_home=self.tmp,
            )

    def test_out_of_range_power_raises(self):
        # Finding 5: power outside 1..1000 is rejected before the kernel boots.
        name = "bad-power"
        self._make_value_material(name, self.tmp, power=5000)
        with self.assertRaises(job_prep_mod.JobPrepError) as ctx:
            job_prep_mod.prepare_job(
                self.svg, self.out,
                machine="sculpfun-s9", material=name,
                color_map={"#ff0000": "cut"}, config_home=self.tmp,
            )
        self.assertIn("power", str(ctx.exception))

    def test_out_of_range_speed_raises(self):
        # Finding 5: speed outside >0 is rejected by the materials layer at
        # save time (fail-closed), so it can never reach prepare_job.
        name = "bad-speed"
        with self.assertRaises(materials_mod.MaterialError) as ctx:
            materials_mod.save_user_material(
                name,
                {
                    "name": name,
                    "description": "bad speed fixture",
                    "machines": {
                        "sculpfun-s9": {
                            "roles": {
                                "cut": {
                                    "kind": "cut", "passes": 1, "power": 650,
                                    "speed": 0.0, "provenance": "tested", "note": "f",
                                }
                            }
                        }
                    },
                },
                config_home=self.tmp,
            )
        self.assertIn("speed", str(ctx.exception))

    def test_out_of_range_passes_raises(self):
        # Finding 5: passes < 1 is rejected by the materials layer at save time
        # (fail-closed), so it can never reach prepare_job.
        name = "bad-passes"
        with self.assertRaises(materials_mod.MaterialError) as ctx:
            materials_mod.save_user_material(
                name,
                {
                    "name": name,
                    "description": "bad passes fixture",
                    "machines": {
                        "sculpfun-s9": {
                            "roles": {
                                "cut": {
                                    "kind": "cut", "passes": 0, "power": 650,
                                    "speed": 16.0, "provenance": "tested", "note": "f",
                                }
                            }
                        }
                    },
                },
                config_home=self.tmp,
            )
        self.assertIn("passes", str(ctx.exception))

    def _run_ladder(self, **overrides):
        kwargs = dict(
            out_dir=self.out, machine="sculpfun-s9", role="cut",
            powers=[100, 200], speed=16.0, passes=1, length=20.0, pitch=6.0,
        )
        kwargs.update(overrides)
        return job_prep_mod.prepare_ladder(**kwargs)

    def test_ladder_length_zero_raises(self):
        # Finding 8: non-positive length is rejected before writing files.
        with self.assertRaises(job_prep_mod.JobPrepError) as ctx:
            self._run_ladder(length=0.0)
        self.assertIn("length", str(ctx.exception))

    def test_ladder_pitch_zero_raises(self):
        with self.assertRaises(job_prep_mod.JobPrepError) as ctx:
            self._run_ladder(pitch=0.0)
        self.assertIn("pitch", str(ctx.exception))

    def test_ladder_passes_zero_raises(self):
        with self.assertRaises(job_prep_mod.JobPrepError) as ctx:
            self._run_ladder(passes=0)
        self.assertIn("passes", str(ctx.exception))

    def test_ladder_negative_geometry_raises(self):
        # Guard the negative tail of each invariant too.
        for kw in ({"length": -5.0}, {"pitch": -2.0}, {"passes": -1}):
            with self.assertRaises(job_prep_mod.JobPrepError):
                self._run_ladder(**kw)


# ── attach_client frame parser (plan Step 9, unit bullets) ──────────────────

_attach_script: list[str] = []


class _AttachHandler(socketserver.StreamRequestHandler):
    def handle(self):
        # Wait for the client's command line so any pre-frame noise the client
        # drains does not shadow the framed reply.
        try:
            self.rfile.readline()
        except OSError:
            return
        for line in _attach_script:
            self.wfile.write((line + "\n").encode("utf-8"))
        self.wfile.flush()


def _start_attach_server(script: list[str]):
    global _attach_script
    _attach_script = script
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _AttachHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


class TestAttachClientFrame(unittest.TestCase):
    """The framed reply parser: valid frame, prose-only refusal, noise+frame."""

    def setUp(self):
        self.servers = []

    def tearDown(self):
        for srv in self.servers:
            srv.shutdown()
            srv.server_close()

    def _serve(self, script):
        srv, port = _start_attach_server(script)
        self.servers.append(srv)
        return port

    def test_valid_frame_parses(self):
        payload = json.dumps({"protocol": 1, "devices": ["sculpfun-s9"]})
        port = self._serve([FRAME_PREFIX + payload])
        reply = attach_send("127.0.0.1", port, "agent status", timeout=2.0)
        self.assertEqual(reply, {"protocol": 1, "devices": ["sculpfun-s9"]})

    def test_prose_only_raises_attacherror(self):
        port = self._serve(["kernel banner line", "warning: warming up"])
        with self.assertRaises(AttachError):
            attach_send("127.0.0.1", port, "agent status", timeout=1.0)

    def test_interleaved_noise_then_frame_parses(self):
        payload = json.dumps({"protocol": 1, "elements": 3})
        port = self._serve(
            ["noisy startup log", "info: channel open", FRAME_PREFIX + payload]
        )
        reply = attach_send("127.0.0.1", port, "agent status", timeout=2.0)
        self.assertEqual(reply, {"protocol": 1, "elements": 3})

class TestStageFileScene(unittest.TestCase):
    """`mk_control._stage_file` against a real backend: load, replace, refuse.

    This is the authoritative coverage for the attach-stage scene contract
    (PR #24 findings 3+4). The consoleserver E2E harness boots a partial kernel
    that does not register the job loader, so scene behaviour is verified here
    against Meerk40tBackend, which loads jobs exactly as a running GUI does.
    """

    def setUp(self):
        import base64 as _b64
        import hashlib as _hl
        import cli_anything.meerk40t.mk_control as _mkc
        self._b64 = _b64
        self._hl = _hl
        self._mkc = _mkc
        self.temp_dir = tempfile.mkdtemp(prefix="mk_stage_")
        self.backend = Meerk40tBackend()
        self.backend.start()

    def tearDown(self):
        self.backend.shutdown()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_job(self):
        d = tempfile.mkdtemp(dir=self.temp_dir)
        svg = os.path.join(d, "in.svg")
        with open(svg, "w", encoding="utf-8") as fh:
            fh.write(
                '<svg xmlns="http://www.w3.org/2000/svg" width="50mm" '
                'height="50mm" viewBox="0 0 50 50">'
                '<rect x="1" y="1" width="10" height="10" fill="none" stroke="#ff0000"/>'
                '<rect x="1" y="15" width="10" height="10" fill="none" stroke="#0000ff"/>'
                '<rect x="1" y="30" width="10" height="10" fill="none" stroke="#000000"/>'
                "</svg>"
            )
        return job_prep_mod.prepare_job(
            svg, d, machine="sculpfun-s9", material="kraft-350gsm",
            allow_estimated=True,
        )["job_svg"]

    def _sha(self, path):
        h = self._hl.sha256()
        with open(path, "rb") as fh:
            h.update(fh.read())
        return h.hexdigest()

    def _b64path(self, path):
        return self._b64.b64encode(os.path.abspath(path).encode("utf-8")).decode("ascii")

    def _stage(self, path, sha):
        return self._mkc._stage_file(self.backend.kernel, self._b64path(path), expected_sha=sha)

    def test_stage_loads_job(self):
        job = self._make_job()
        reply = self._stage(job, self._sha(job))
        self.assertIsNone(reply.get("error"))
        self.assertEqual(reply["elements"], 3)
        self.assertEqual(len(reply["operations"]), 3)

    def test_stage_replaces_scene_no_accumulation(self):
        job_a = self._make_job()
        job_b = self._make_job()
        first = self._stage(job_a, self._sha(job_a))
        second = self._stage(job_b, self._sha(job_b))
        # The second stage must REPLACE, not accumulate: staging two jobs still
        # leaves exactly one job's operations and elements in the scene.
        self.assertEqual(second["elements"], first["elements"])
        self.assertEqual(len(second["operations"]), len(first["operations"]))
        self.assertEqual(second["elements"], 3)

    def test_stage_hash_mismatch_refused_scene_untouched(self):
        job = self._make_job()
        self._stage(job, self._sha(job))
        el = self.backend.kernel.elements
        before_ops = len(list(el.ops()))
        before_elems = len(list(el.elems()))
        # A wrong expected hash must return an error frame and leave the scene
        # untouched (nothing loaded, nothing removed).
        reply = self._stage(job, "0" * 64)
        self.assertIn("error", reply)
        self.assertIn("hash", reply["error"].lower())
        self.assertEqual(len(list(el.ops())), before_ops)
        self.assertEqual(len(list(el.elems())), before_elems)


class TestSaveVerification(BackendTestCase):
    """RED regressions for hardened save_svg / load_file (issue #26)."""

    def test_save_svg_rejects_unsupported_version_preserves_bytes(self):
        from cli_anything.meerk40t.utils.meerk40t_backend import SaveVerificationError
        path = self.temp_path("out.svg")
        self.backend.run("circle 1in 1in 1in")
        self.assertTrue(self.backend.save_svg(path))
        before = open(path, "rb").read()
        # An unsupported version must fail before any write and leave bytes intact.
        with self.assertRaises(SaveVerificationError):
            self.backend.save_svg(path, version="bogus")
        self.assertEqual(open(path, "rb").read(), before)

    def test_save_svg_rejects_missing_output(self):
        from cli_anything.meerk40t.utils.meerk40t_backend import SaveVerificationError
        from unittest.mock import patch
        path = self.temp_path("out.svg")
        self.backend.run("circle 1in 1in 1in")
        # Simulate the backend failing to produce any output for the target path.
        with patch.object(self.backend, "run", lambda cmd: None):
            with self.assertRaises(SaveVerificationError):
                self.backend.save_svg(path)

    def test_save_svg_write_failure_preserves_existing_bytes(self):
        from cli_anything.meerk40t.utils.meerk40t_backend import SaveVerificationError
        from unittest.mock import patch
        path = self.temp_path("out.svg")
        self.backend.run("circle 1in 1in 1in")
        self.assertTrue(self.backend.save_svg(path))
        before = open(path, "rb").read()
        # Simulate the backend writing an EMPTY file where it should have saved.
        def fake_run(cmd):
            target = cmd.split(" ", 1)[1] if " " in cmd else cmd
            open(target, "w").close()
            return []
        with patch.object(self.backend, "run", fake_run):
            with self.assertRaises(SaveVerificationError):
                self.backend.save_svg(path)
        # A failed write must never corrupt or truncate the pre-existing file.
        self.assertEqual(open(path, "rb").read(), before)

    def test_load_file_rejects_failed_load(self):
        from cli_anything.meerk40t.utils.meerk40t_backend import LoadError
        from unittest.mock import patch
        path = self.temp_path("roundtrip.svg")
        self.backend.run("circle 1in 1in 1in")
        self.assertTrue(self.backend.save_svg(path))

        def fake_run(cmd):
            self.backend._captured.append("Error: cannot parse file")
            return self.backend._captured

        with patch.object(self.backend, "run", fake_run):
            with self.assertRaises(LoadError):
                self.backend.load_file(path)

    def test_save_svgz_default_roundtrips(self):
        import gzip
        path = self.temp_path("proj.svgz")
        self.backend.run("circle 1in 1in 1in")
        # A default .svgz save must select the compressed saver and produce a
        # valid gzip artifact (regression: default omitted -v compressed).
        self.assertTrue(self.backend.save_svg(path))
        with gzip.open(path, "rb") as g:
            self.assertIn(b"<svg", g.read(4096))

    def test_load_file_allows_error_named_path(self):
        # A path whose name contains "exception"/"error" must not be misread as
        # a load failure from the echoed command line (regression).
        path = self.temp_path("exception-error.svg")
        self.backend.run("circle 1in 1in 1in")
        self.assertTrue(self.backend.save_svg(path))
        self.backend.run("elements clear all")
        self.assertTrue(self.backend.load_file(path))
        self.assertGreaterEqual(self.backend.elem_count(), 1)


class TestCommandOutcomeBoundary(BackendTestCase):
    """RED regressions for the single _complete_command boundary (issue #26)."""

    def _dummy_ctx(self, project_path=None, session=None, dry_run=False):
        import click
        backend = self.backend

        class DummyContext(click.Context):
            def __init__(self):
                self.obj = {
                    "backend": backend,
                    "session": session,
                    "project_path": project_path,
                    "dry_run": dry_run,
                    "json": True,
                }

            def exit(self, code=0):
                raise SystemExit(code)

        return DummyContext()

    def test_success_autosaves_exactly_once(self):
        from cli_anything.meerk40t.meerk40t_cli import _complete_command
        from unittest.mock import patch
        path = self.temp_path("proj.svg")
        self.backend.run("circle 1in 1in 1in")
        self.assertTrue(self.backend.save_svg(path))
        ctx = self._dummy_ctx(project_path=path)
        with patch.object(self.backend, "save_svg", wraps=self.backend.save_svg) as spy:
            with self.assertRaises(SystemExit) as cm:
                _complete_command(ctx, {"ok": True, "saved": True}, mutating=True)
            self.assertEqual(cm.exception.code, 0)
            spy.assert_called_once()

    def test_failure_does_not_autosave(self):
        from cli_anything.meerk40t.meerk40t_cli import _complete_command
        from unittest.mock import patch
        path = self.temp_path("proj.svg")
        ctx = self._dummy_ctx(project_path=path)
        with patch.object(self.backend, "save_svg") as spy:
            with self.assertRaises(SystemExit) as cm:
                _complete_command(ctx, {"error": "boom"}, mutating=True)
            self.assertEqual(cm.exception.code, 1)
            spy.assert_not_called()

    def test_persistence_failure_converts_to_failure(self):
        from cli_anything.meerk40t.meerk40t_cli import _complete_command
        from cli_anything.meerk40t.utils.meerk40t_backend import (
            BackendError,
            SaveVerificationError,
        )
        from unittest.mock import patch
        path = self.temp_path("proj.svg")
        ctx = self._dummy_ctx(project_path=path)
        with patch.object(
            self.backend, "save_svg", side_effect=SaveVerificationError("nope", path=path)
        ):
            with self.assertRaises(SystemExit) as cm:
                _complete_command(ctx, {"ok": True}, mutating=True)
            self.assertEqual(cm.exception.code, 1)
        # The boundary must treat BackendError as a failure, not crash.
        self.assertTrue(issubclass(SaveVerificationError, BackendError))

    def test_repl_routes_through_boundary(self):
        from cli_anything.meerk40t.meerk40t_cli import _dispatch_repl, _complete_command
        from unittest.mock import patch
        path = self.temp_path("repl.svg")
        self.backend.run("circle 1in 1in 1in")
        self.assertTrue(self.backend.save_svg(path))
        ctx = self._dummy_ctx(project_path=path)
        with patch(
            "cli_anything.meerk40t.meerk40t_cli._complete_command"
        ) as spy_complete:
            _dispatch_repl(ctx, "elements translate 0 10mm 20mm", None, {})
            spy_complete.assert_called()
            _, kwargs = spy_complete.call_args
            self.assertTrue(kwargs.get("mutating"))

    def test_repl_mutation_persists(self):
        from cli_anything.meerk40t.meerk40t_cli import _dispatch_repl
        from unittest.mock import patch
        path = self.temp_path("repl.svg")
        self.backend.run("circle 1in 1in 1in")
        self.assertTrue(self.backend.save_svg(path))
        ctx = self._dummy_ctx(project_path=path)
        with patch.object(self.backend, "save_svg", wraps=self.backend.save_svg) as spy:
            _dispatch_repl(ctx, "elements translate 0 10mm 20mm", None, {})
        spy.assert_called_once()

    def test_empty_error_string_is_failure(self):
        from cli_anything.meerk40t.meerk40t_cli import _complete_command
        from unittest.mock import patch
        path = self.temp_path("proj.svg")
        ctx = self._dummy_ctx(project_path=path)
        # An error payload with an empty message (e.g. RuntimeError()) is still a
        # failure: nonzero exit and no autosave.
        with patch.object(self.backend, "save_svg") as spy:
            with self.assertRaises(SystemExit) as cm:
                _complete_command(ctx, {"error": ""}, mutating=True)
            self.assertEqual(cm.exception.code, 1)
            spy.assert_not_called()

    def test_none_error_is_success(self):
        from cli_anything.meerk40t.meerk40t_cli import _complete_command
        from unittest.mock import patch
        path = self.temp_path("proj.svg")
        self.backend.run("circle 1in 1in 1in")
        self.assertTrue(self.backend.save_svg(path))
        ctx = self._dummy_ctx(project_path=path)
        # error=None is the device-acknowledgement success sentinel, not failure.
        with patch.object(self.backend, "save_svg", wraps=self.backend.save_svg) as spy:
            with self.assertRaises(SystemExit) as cm:
                _complete_command(ctx, {"error": None, "ok": True}, mutating=True)
            self.assertEqual(cm.exception.code, 0)
            spy.assert_called_once()

    def test_repl_project_open_persists_session(self):
        from cli_anything.meerk40t.meerk40t_cli import _dispatch_repl
        from cli_anything.meerk40t.core import session as session_mod
        from unittest.mock import patch
        src = self.temp_path("src.svg")
        self.backend.run("circle 1in 1in 1in")
        self.assertTrue(self.backend.save_svg(src))
        sess = session_mod.Session(self.temp_path("sess.json"))
        ctx = self._dummy_ctx(session=sess)
        # Opening a project changes the session's SVG association, so the REPL
        # must treat it as mutating and persist it (regression: was read-only).
        with patch.object(self.backend, "save_svg", wraps=self.backend.save_svg) as spy:
            _dispatch_repl(ctx, f"project open {src}", None, {})
        spy.assert_called_once()

# ── Issue #27: transactional project/session lifecycle regressions ──────────


class _FakeBackend:
    """Minimal backend stand-in for session-persistence tests (no kernel)."""

    def __init__(self):
        self.saved = []
        self.fail_next = False

    def elem_count(self):
        return 0

    def op_count(self):
        return 0

    def save_svg(self, path, version="default"):
        if self.fail_next:
            raise SaveVerificationError("injected save failure", path=path)
        with open(path, "w", encoding="utf-8") as f:
            f.write("<svg></svg>")
        self.saved.append(path)
        return True


class TestProjectTransaction(BackendTestCase):
    """Transactional open/save/close: failure must not destroy prior state."""

    def _seed(self, kind="circle"):
        if kind == "circle":
            elements.add_circle(self.backend, "1in", "1in", "1in")
        else:
            elements.add_rect(self.backend, "0in", "0in", "1in", "1in")
        self.assertEqual(self.backend.elem_count(), 1)

    def test_failed_open_rolls_back_prior_scene(self):
        self._seed()
        real_load = self.backend.load_file

        def boom(path):
            raise LoadError("injected load failure", path=path)

        self.backend.load_file = boom
        existing = self.temp_path("exists.svg")
        with open(existing, "w", encoding="utf-8") as f:
            f.write("<svg></svg>")
        try:
            result = project.open_project(self.backend, existing)
        finally:
            self.backend.load_file = real_load
        self.assertIsNotNone(result.get("error"), result)
        self.assertFalse(result.get("ok", True), result)
        self.assertEqual(self.backend.elem_count(), 1)

    def test_malformed_svg_open_fails_and_preserves(self):
        self._seed()
        bad = self.temp_path("bad.svg")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("this is not an svg")
        result = project.open_project(self.backend, bad)
        self.assertIsNotNone(result.get("error"), result)
        self.assertEqual(self.backend.elem_count(), 1)

    def test_loader_diagnostic_failure_rolls_back(self):
        self._seed()
        real_load = self.backend.load_file

        def diag(path):
            raise LoadError("BadFileError: malformed elements", path=path)

        self.backend.load_file = diag
        existing = self.temp_path("exists.svg")
        with open(existing, "w", encoding="utf-8") as f:
            f.write("<svg></svg>")
        try:
            result = project.open_project(self.backend, existing)
        finally:
            self.backend.load_file = real_load
        self.assertIsNotNone(result.get("error"), result)
        self.assertEqual(self.backend.elem_count(), 1)

    def test_save_failure_keeps_prior_bytes(self):
        a = self.temp_path("A.svg")
        self._seed()
        self.backend.save_svg(a, "default")
        before = cli_mod._sha256_file(a)
        self.assertIsNotNone(before)
        real_save = self.backend.save_svg

        def fail(path, version="default"):
            raise SaveVerificationError("injected save failure", path=path)

        self.backend.save_svg = fail
        try:
            result = project.save_project(self.backend, a, version="default")
        finally:
            self.backend.save_svg = real_save
        self.assertIsNotNone(result.get("error"), result)
        self.assertEqual(cli_mod._sha256_file(a), before)

    def test_successful_open_postconditions(self):
        b = self.temp_path("B.svg")
        self._seed()
        self.backend.save_svg(b, "default")
        project.create_project(self.backend)
        self.assertEqual(self.backend.elem_count(), 0)
        result = project.open_project(self.backend, b)
        self.assertIsNone(result.get("error"), result)
        self.assertEqual(result["path"], b)
        self.assertGreaterEqual(self.backend.elem_count(), 1)

    def test_failed_close_surfaces_error(self):
        self._seed()
        real_clear = project._clear_elements_tree
        project._clear_elements_tree = lambda backend: None
        try:
            result = project.close_project(self.backend)
        finally:
            project._clear_elements_tree = real_clear
        self.assertIsNotNone(result.get("error"), result)
        self.assertFalse(result.get("ok", True), result)

    def test_close_postconditions(self):
        self._seed()
        result = project.close_project(self.backend)
        self.assertTrue(result.get("closed"), result)
        self.assertEqual(self.backend.elem_count(), 0)


class TestSessionPersistence(unittest.TestCase):
    """Atomic session JSON, coordinated SVG, and corruption surfacing."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="mk_sess_")
        self.session_path = os.path.join(self.temp_dir, "session.json")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_session(self):
        with open(self.session_path, "w", encoding="utf-8") as f:
            f.write('{"name": "prior", "svg_path": null}')
        return session.Session(self.session_path)

    def test_atomic_json_write_keeps_prior_on_replace_failure(self):
        sess = self._make_session()
        sess.name = "next"
        real_replace = os.replace

        def boom(src, dst):
            raise OSError("injected replace failure")

        os.replace = boom
        try:
            with self.assertRaises(OSError):
                sess.save(backend=None)
        finally:
            os.replace = real_replace
        with open(self.session_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"name": "prior", "svg_path": null}')

    def test_surfaced_persistence_error_not_swallowed(self):
        sess = self._make_session()
        sess.name = "next"
        real_fsync = os.fsync

        def boom(fd):
            raise OSError("injected fsync failure")

        os.fsync = boom
        try:
            with self.assertRaises(OSError):
                sess.save(backend=None)
        finally:
            os.fsync = real_fsync
        # The atomic-write path must surface the error (not swallow it) and the
        # prior file bytes must remain intact (AC5).
        with open(self.session_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"name": "prior", "svg_path": null}')

    def test_session_init_surfaces_corruption(self):
        bad = os.path.join(self.temp_dir, "corrupt.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json")
        with self.assertRaises(BackendError):
            session.Session(bad)

    def test_session_init_loads_valid_file(self):
        sess = self._make_session()
        self.assertEqual(sess.name, "prior")


class TestSessionRestore(BackendTestCase):
    """Session-only SVG restore (the --session alone path)."""

    def test_session_only_restore_reloads_recorded_svg(self):
        f = self.temp_path("recorded.svg")
        elements.add_circle(self.backend, "1in", "1in", "1in")
        self.backend.save_svg(f, "default")
        self.assertGreaterEqual(self.backend.elem_count(), 1)
        project.create_project(self.backend)
        self.assertEqual(self.backend.elem_count(), 0)
        sessfile = self.temp_path("s.json")
        with open(sessfile, "w", encoding="utf-8") as fh:
            json.dump({"name": "restored", "svg_path": f}, fh)
        sess = session.Session(sessfile)
        result = sess.restore(self.backend)
        self.assertIsNone(result.get("error"), result)
        self.assertGreaterEqual(self.backend.elem_count(), 1)


class TestCliPrecedence(BackendTestCase):
    """Deterministic --project/--session precedence and no stale autosave."""

    def _run_json(self, args):
        import io
        capture = io.StringIO()
        orig = cli_mod._REAL_STDOUT
        cli_mod._REAL_STDOUT = capture
        try:
            from click.testing import CliRunner
            result = CliRunner().invoke(cli_mod.cli, ["--json"] + args)
        finally:
            cli_mod._REAL_STDOUT = orig
            sys.stdout = orig
        return result, capture.getvalue()

    def test_project_open_changes_only_target_not_existing(self):
        a = self.temp_path("A.svg")
        b = self.temp_path("B.svg")
        elements.add_circle(self.backend, "1in", "1in", "1in")
        self.backend.save_svg(a, "default")
        project.create_project(self.backend)
        elements.add_rect(self.backend, "0in", "0in", "1in", "1in")
        self.backend.save_svg(b, "default")
        before_a = cli_mod._sha256_file(a)
        result, out = self._run_json(["--project", a, "project", "open", b])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(cli_mod._sha256_file(a), before_a, "stale autosave wrote A")

    def test_session_alone_restores_recorded_svg(self):
        f = self.temp_path("recorded.svg")
        elements.add_circle(self.backend, "1in", "1in", "1in")
        self.backend.save_svg(f, "default")
        sessfile = self.temp_path("sess.json")
        with open(sessfile, "w", encoding="utf-8") as fh:
            json.dump({"name": "s", "svg_path": f}, fh)
        result, out = self._run_json(["--session", sessfile, "project", "info"])
        self.assertEqual(result.exit_code, 0, result.output)
        data = json.loads(out)
        self.assertGreaterEqual(data["elements"], 1, "session SVG not restored")

    def test_explicit_project_wins_over_session(self):
        # Session would restore G (1 element); explicit --project H loads a
        # different count (2). Precedence must yield H's count, never G's.
        g = self.temp_path("G.svg")
        elements.add_circle(self.backend, "1in", "1in", "1in")
        self.backend.save_svg(g, "default")
        # Start H from a clean tree so its element count is exactly 2.
        self.backend.elements.clear_all()
        h = self.temp_path("H.svg")
        elements.add_circle(self.backend, "0in", "0in", "1in")
        elements.add_circle(self.backend, "2in", "2in", "1in")
        self.backend.save_svg(h, "default")
        sessfile = self.temp_path("sess2.json")
        with open(sessfile, "w", encoding="utf-8") as fh:
            json.dump({"name": "s", "svg_path": g}, fh)
        result, out = self._run_json(
            ["--session", sessfile, "--project", h, "project", "info"]
        )
        self.assertEqual(result.exit_code, 0, result.output)
        data = json.loads(out)
        # Explicit --project must win: H loaded (2), not the session's G (1).
        self.assertEqual(data["elements"], 2, "explicit --project did not win")
        self.assertNotEqual(data["elements"], 1, "session SVG leaked through")

    def _repl_ctx(self, project_path=None, session=None):
        import click
        test = self

        class DummyContext(click.Context):
            def __init__(self):
                self.obj = {
                    "backend": test.backend,
                    "session": session,
                    "project_path": project_path,
                    "dry_run": False,
                    "json": True,
                }

            def exit(self, code=0):
                raise SystemExit(code)

        return DummyContext()

    def test_repl_failed_open_keeps_prior_binding(self):
        from cli_anything.meerk40t.meerk40t_cli import _dispatch_repl
        from cli_anything.meerk40t.core import session as session_mod
        from unittest.mock import patch
        a = self.temp_path("A.svg")
        elements.add_circle(self.backend, "1in", "1in", "1in")
        self.backend.save_svg(a, "default")
        sess = session_mod.Session(self.temp_path("sess.json"))
        sess.svg_path = a
        ctx = self._repl_ctx(project_path=a, session=sess)
        # Prior binding is A.
        self.assertEqual(sess.svg_path, a)
        # Open an existing but malformed B: open_project fails and must NOT
        # rebind to B (AC2). A missing path would be treated as a valid new
        # project, so B must be a real invalid file.
        b = self.temp_path("B_bad.svg")
        with open(b, "w", encoding="utf-8") as fh:
            fh.write("this is not an svg")
        _dispatch_repl(ctx, f"project open {b}", None, {})
        self.assertEqual(sess.svg_path, a, "failed open leaked binding to B")
        self.assertEqual(ctx.obj["project_path"], a)
        # A subsequent successful mutating REPL line must autosave into A, not B.
        with patch.object(self.backend, "save_svg", wraps=self.backend.save_svg) as spy:
            _dispatch_repl(ctx, "elements translate 0 10mm 20mm", None, {})
        saved_paths = [c.args[0] for c in spy.call_args_list]
        self.assertTrue(saved_paths, "no autosave occurred")
        self.assertIn(a, saved_paths, "did not autosave into prior project A")
        self.assertNotIn(b, saved_paths, "autosaved into the failed-open target B")

if __name__ == "__main__":
    unittest.main()
class TestMaterialAtomicity(BackendTestCase):
    """Issue #29: atomic, fail-closed material loading and writes.

    RED: every behavioral test fails before the strict loader / atomic writer
    land (load silently returns bad data; save is non-atomic and unvalidated).
    GREEN: passes after.
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="mat-at-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    # ── helpers ──────────────────────────────────────────────────────────
    def _role(self, **over):
        r = {"kind": "cut", "passes": 1, "power": 500, "speed": 16.0,
             "provenance": "estimated", "note": "t"}
        r.update(over)
        return r

    def _wrap(self, name, role):
        return {"name": name, "machines": {"sculpfun-s9": {"roles": {"cut": role}}}}

    def _write_raw(self, name, text):
        d = os.path.join(self.tmp, "materials")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"{name}.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def _write_material(self, name, role_over=None, *, drop_role=(), drop_top=()):
        role = self._role(**(role_over or {}))
        for k in drop_role:
            role.pop(k, None)
        data = self._wrap(name, role)
        for k in drop_top:
            data.pop(k, None)
        return self._write_raw(name, json.dumps(data))

    def _save_valid(self, name="good"):
        return materials_mod.save_user_material(
            name, self._wrap(name, self._role()), config_home=self.tmp
        )

    # ── Group A: non-finite values must be rejected (load side) ──────────
    def test_nan_power_rejected(self):
        self._write_material("nanpow", {"power": float("nan")})
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.load_material("nanpow", config_home=self.tmp)

    def test_infinity_speed_rejected(self):
        self._write_material("infspeed", {"speed": float("inf")})
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.load_material("infspeed", config_home=self.tmp)

    def test_neg_infinity_passes_rejected(self):
        self._write_material("neginfpass", {"passes": float("-inf")})
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.load_material("neginfpass", config_home=self.tmp)

    # ── Group B: invalid values rejected (load side) ─────────────────────
    def test_non_numeric_power_rejected(self):
        self._write_material("strpow", {"power": "high"})
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.load_material("strpow", config_home=self.tmp)

    def test_zero_power_rejected(self):
        self._write_material("zeropow", {"power": 0})
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.load_material("zeropow", config_home=self.tmp)

    def test_negative_power_rejected(self):
        self._write_material("negpow", {"power": -5})
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.load_material("negpow", config_home=self.tmp)

    def test_zero_speed_rejected(self):
        self._write_material("zerospeed", {"speed": 0})
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.load_material("zerospeed", config_home=self.tmp)

    def test_negative_speed_rejected(self):
        self._write_material("negspeed", {"speed": -1.0})
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.load_material("negspeed", config_home=self.tmp)

    def test_zero_passes_rejected(self):
        self._write_material("zeropass", {"passes": 0})
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.load_material("zeropass", config_home=self.tmp)

    def test_negative_passes_rejected(self):
        self._write_material("negpass", {"passes": -2})
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.load_material("negpass", config_home=self.tmp)

    # ── Group C: malformed nested structure rejected (load side) ─────────
    def test_missing_machines_rejected(self):
        self._write_material("nomach", drop_top=("machines",))
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.load_material("nomach", config_home=self.tmp)

    def test_machines_not_dict_rejected(self):
        self._write_raw("machlist", json.dumps({"name": "machlist",
            "machines": ["sculpfun-s9"]}))
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.load_material("machlist", config_home=self.tmp)

    def test_roles_not_dict_rejected(self):
        self._write_raw("rolelist", json.dumps({"name": "rolelist",
            "machines": {"sculpfun-s9": {"roles": []}}}))
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.load_material("rolelist", config_home=self.tmp)

    def test_role_missing_kind_rejected(self):
        self._write_material("nokind", drop_role=("kind",))
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.load_material("nokind", config_home=self.tmp)

    # ── Group D: fail-closed precedence + preservation ───────────────────
    def test_corrupt_user_override_raises_not_falls_back(self):
        # A corrupt user override of a BUNDLED name must raise, never return
        # the bundled material (closes the silent-fallback bug).
        self._write_raw("kraft-350gsm", "{ not valid json")
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.load_material("kraft-350gsm", config_home=self.tmp)

    def test_corrupt_user_override_preserves_prior_bytes(self):
        p = self._save_valid("mypreserve")
        original = open(p, "rb").read()
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("{ corrupt")
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.load_material("mypreserve", config_home=self.tmp)
        # File must NOT have been repaired or truncated to something else.
        after = open(p, "rb").read()
        self.assertEqual(after, b"{ corrupt")
        self.assertNotEqual(after, original)

    # ── Group E: atomic write cannot corrupt the prior file ──────────────
    def test_atomic_replace_failure_preserves_prior(self):
        p = self._save_valid("apsafe")
        prior = open(p, "rb").read()

        def boom(src, dst):
            raise OSError("disk full")

        with patch("os.replace", boom):
            with self.assertRaises(OSError):
                materials_mod.save_user_material(
                    "apsafe", self._wrap("apsafe", self._role(power=700)),
                    config_home=self.tmp)
        self.assertEqual(open(p, "rb").read(), prior)

    def test_atomic_fsync_failure_preserves_prior(self):
        p = self._save_valid("afsafe")
        prior = open(p, "rb").read()

        def boom(fd):
            raise OSError("fsync failed")

        with patch("os.fsync", boom):
            with self.assertRaises(OSError):
                materials_mod.save_user_material(
                    "afsafe", self._wrap("afsafe", self._role(power=710)),
                    config_home=self.tmp)
        self.assertEqual(open(p, "rb").read(), prior)

    def test_atomic_temp_cleaned_on_failure(self):
        def boom(src, dst):
            raise OSError("replace failed")

        with patch("os.replace", boom):
            with self.assertRaises(OSError):
                materials_mod.save_user_material(
                    "aclean", self._wrap("aclean", self._role()),
                    config_home=self.tmp)
        tmp_files = [f for f in os.listdir(os.path.join(self.tmp, "materials"))
                     if ".tmp-" in f or f.endswith(".tmp")]
        self.assertEqual(tmp_files, [])

    def test_atomic_swap_keeps_reader_consistent(self):
        # At the moment of swap the destination still holds the OLD, complete,
        # parseable content — never a truncated temp file.
        p = self._save_valid("aswap")
        real_replace = os.replace
        seen = {}

        def spy(src, dst):
            with open(dst, "r", encoding="utf-8") as fh:
                seen["parsed"] = json.load(fh)
            return real_replace(src, dst)

        with patch("os.replace", spy):
            materials_mod.save_user_material(
                "aswap", self._wrap("aswap", self._role(power=1)),
                config_home=self.tmp)
        self.assertIn("parsed", seen, "os.replace was never used by the writer")
        self.assertEqual(seen["parsed"]["machines"]["sculpfun-s9"]
                         ["roles"]["cut"]["power"], 500)

    # ── Group F: contract preservation (green both before and after) ──────
    def test_valid_material_round_trips(self):
        self._save_valid("rt")
        mat = materials_mod.load_material("rt", config_home=self.tmp)
        self.assertIsNotNone(mat)
        role = mat["machines"]["sculpfun-s9"]["roles"]["cut"]
        self.assertEqual(role["power"], 500)
        self.assertEqual(role["speed"], 16.0)
        self.assertEqual(role["passes"], 1)

    def test_unknown_name_returns_none(self):
        self.assertIsNone(
            materials_mod.load_material("ghost", config_home=self.tmp))

    def test_list_materials_skips_corrupt(self):
        self._save_valid("ok1")
        self._write_raw("broken", "{ broken json")
        names = [m["name"] for m in materials_mod.list_materials(config_home=self.tmp)]
        self.assertIn("ok1", names)
        self.assertNotIn("broken", names)

    # ── Group AC8: save validates before writing, never mutates prior ────
    def test_save_rejects_invalid_keeps_prior(self):
        p = self._save_valid("ac8keep")
        with open(p, "rb") as fh:
            prior = fh.read()
        bad = self._wrap("ac8keep", self._role(power=0))
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.save_user_material("ac8keep", bad, config_home=self.tmp)
        with open(p, "rb") as fh:
            self.assertEqual(fh.read(), prior)

    def test_save_rejects_invalid_new_material(self):
        bad = self._wrap("ac8new", self._role(power=-1))
        with self.assertRaises(materials_mod.MaterialError):
            materials_mod.save_user_material("ac8new", bad, config_home=self.tmp)
        self.assertFalse(
            os.path.exists(os.path.join(self.tmp, "materials", "ac8new.json")))
