"""Unit tests for cli-anything-meerk40t core modules.

Each test uses the real Meerk40tBackend booted headlessly. No external test
frameworks are required; only the standard library unittest is used.
"""

from __future__ import annotations

import os
import json
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET

from cli_anything.meerk40t.core import elements
from cli_anything.meerk40t.core import export
from cli_anything.meerk40t.core import operations
from cli_anything.meerk40t.core import project
from cli_anything.meerk40t.core import session
from cli_anything.meerk40t.core import device as device_mod
from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend


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
        result, out = self._run_json(["device", "connect"])
        self.assertEqual(result.exit_code, 0, result.output)
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


if __name__ == "__main__":
    unittest.main()
