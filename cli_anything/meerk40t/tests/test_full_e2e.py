"""End-to-end and subprocess tests for cli-anything-meerk40t.

These tests exercise the installed CLI entry point (or the module fallback) and
the real Meerk40tBackend in realistic workflows. Only unittest is used.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

from cli_anything.meerk40t.core import elements
from cli_anything.meerk40t.core import export
from cli_anything.meerk40t.core import operations
from cli_anything.meerk40t.core import project
from cli_anything.meerk40t.utils.meerk40t_backend import Meerk40tBackend

# Ensure the venv's bin directory is on PATH so _resolve_cli can find the
# installed console script even when running under `python -m unittest`.
_BIN_DIR = os.path.dirname(sys.executable)
os.environ["PATH"] = _BIN_DIR + os.pathsep + os.environ.get("PATH", "")


def _resolve_cli(name):
    """Return the command list to run the CLI, preferring an installed script."""
    import shutil  # noqa: F401

    force = os.environ.get("CLI_ANYTHING_FORCE_INSTALLED", "").strip() == "1"
    path = shutil.which(name)
    if path:
        print(f"[_resolve_cli] Using installed: {path}")
        return [path]
    if force:
        raise RuntimeError(f"{name} not in PATH")
    module = "cli_anything.meerk40t.meerk40t_cli"
    print(f"[_resolve_cli] Fallback: {sys.executable} -m {module}")
    return [sys.executable, "-m", module]


class TestCLISubprocess(unittest.TestCase):
    CLI_BASE = _resolve_cli("cli-anything-meerk40t")

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="mk_e2e_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run(self, args):
        """Run the CLI with the given args and return a CompletedProcess."""
        cmd = self.CLI_BASE + args
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def _json(self, args):
        """Run the CLI with --json and parse the stdout as JSON."""
        result = self._run(["--json"] + args)
        self.assertEqual(
            result.returncode,
            0,
            f"Command failed: {args}\nstdout: {result.stdout}\nstderr: {result.stderr}",
        )
        self.assertTrue(
            result.stdout.strip(),
            f"No JSON output for {args}",
        )
        return json.loads(result.stdout)

    def test_help(self):
        result = self._run(["--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn("project", result.stdout.lower())

    def test_project_new_json(self):
        data = self._json(["project", "new"])
        self.assertIn("elements", data)
        self.assertIn("operations", data)
        self.assertEqual(data["elements"], 0)
        self.assertEqual(data["operations"], 0)

    def test_elements_circle_json(self):
        data = self._json(["elements", "circle", "1in", "1in", "1in"])
        self.assertTrue(data["added"])
        self.assertEqual(data["type"], "circle")
        self.assertGreater(data["total_elements"], 0)

    def test_elements_rect_stroke_fill(self):
        data = self._json(
            [
                "elements",
                "rect",
                "2in",
                "2in",
                "1in",
                "1in",
                "--stroke",
                "red",
                "--fill",
                "blue",
            ]
        )
        self.assertTrue(data["added"])
        self.assertEqual(data["type"], "rect")

    def test_elements_list(self):
        data = self._json(["elements", "list"])
        self.assertIsInstance(data, list)

    def test_export_svg(self):
        path = os.path.join(self.temp_dir, "mk_e2e_out.svg")
        data = self._json(["export", "svg", path])
        self.assertIn("size_bytes", data)
        self.assertGreater(data["size_bytes"], 0)
        self.assertTrue(os.path.exists(path))
        root = ET.parse(path).getroot()
        self.assertTrue(root.tag.endswith("svg"))

    def test_console_passthrough(self):
        data = self._json(["console", "circle 2in 2in 1in"])
        self.assertIn("output", data)
        self.assertIsInstance(data["output"], list)
        self.assertIn("circle 2in 2in 1in", data["command"])

    def test_persistence(self):
        path = os.path.join(self.temp_dir, "mk_e2e_p.svg")
        add_result = self._json(
            ["-p", path, "elements", "circle", "1in", "1in", "1in"]
        )
        self.assertTrue(add_result["added"])
        list_result = self._json(["-p", path, "elements", "list"])
        self.assertIsInstance(list_result, list)
        self.assertGreater(len(list_result), 0)

    def test_elements_transformations_cli(self):
        path = os.path.join(self.temp_dir, "mk_e2e_trans.svg")
        
        # 1. Add elements
        self._json(["-p", path, "elements", "circle", "0in", "0in", "1in"])
        self._json(["-p", path, "elements", "circle", "2in", "2in", "1in"])
        
        # 2. Translate
        res = self._json(["-p", path, "elements", "translate", "0", "10mm", "20mm"])
        self.assertTrue(res["translated"])
        self.assertEqual(res["index"], 0)
        
        # 3. Scale
        res = self._json(["-p", path, "elements", "scale", "1", "2.0"])
        self.assertTrue(res["scaled"])
        self.assertEqual(res["index"], 1)
        
        # 4. Rotate
        res = self._json(["-p", path, "elements", "rotate", "0", "90deg"])
        self.assertTrue(res["rotated"])
        self.assertEqual(res["index"], 0)
        
        # 5. Align
        res = self._json(["-p", path, "elements", "align", "center"])
        self.assertTrue(res["aligned"])
        self.assertEqual(res["num_elements"], 2)
        
        # 6. Group & Ungroup
        res = self._json(["-p", path, "elements", "group", "-l", "MyGroup"])
        self.assertTrue(res["grouped"])
        self.assertEqual(res["num_elements"], 2)
        
        res = self._json(["-p", path, "elements", "ungroup"])
        self.assertTrue(res["ungrouped"])

    def test_operations_management_cli(self):
        path = os.path.join(self.temp_dir, "mk_e2e_ops.svg")
        
        # 1. Add operations
        res = self._json(["-p", path, "operations", "add", "cut"])
        self.assertTrue(res["added"])
        self.assertEqual(res["type"], "cut")
        
        res = self._json(["-p", path, "operations", "add", "engrave"])
        self.assertTrue(res["added"])
        self.assertEqual(res["type"], "engrave")
        
        # 2. Delete operation
        res = self._json(["-p", path, "operations", "delete", "0"])
        self.assertTrue(res["deleted"])
        self.assertEqual(res["index"], 0)
        
        # 3. Clear operations
        res = self._json(["-p", path, "operations", "clear"])
        self.assertTrue(res["cleared"])
        self.assertEqual(res["total_ops"], 0)


class TestBackendE2E(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="mk_e2e_backend_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def temp_path(self, filename):
        return os.path.join(self.temp_dir, filename)

    def test_gcode_export_with_grbl(self):
        backend = Meerk40tBackend()
        backend.start()
        try:
            elements.add_circle(backend, "1in", "1in", "1in")
            operations.classify_elements(backend)
            backend.run("service device start -i grbl")
            self.assertIn("grbl", str(backend.device()).lower())
            operations.set_operation(backend, 0, "power", 150)

            path = self.temp_path("mk_e2e.gcode")
            result = export.export_gcode(backend, path)
            self.assertEqual(result["format"], "gcode")
            self.assertTrue(os.path.exists(path))
            self.assertGreater(result["size_bytes"], 0)

            with open(path, "r", encoding="utf-8", errors="replace") as f:
                sample = f.read()
            self.assertTrue(
                any(token in sample for token in ("G90", "G0", "M4")),
                f"G-code sample did not contain expected tokens: {sample[:200]!r}",
            )
        finally:
            backend.shutdown()

    def test_svg_round_trip(self):
        path = self.temp_path("round_trip.svg")

        b1 = Meerk40tBackend()
        b1.start()
        try:
            elements.add_circle(b1, "1in", "1in", "1in")
            elements.add_rect(b1, "0.5in", "0.5in", "1in", "1in")
            self.assertGreaterEqual(b1.elem_count(), 2)
            b1.save_svg(path)
        finally:
            b1.shutdown()

        b2 = Meerk40tBackend()
        b2.start()
        try:
            b2.load_file(path)
            self.assertGreaterEqual(b2.elem_count(), 2)
        finally:
            b2.shutdown()

    def test_full_workflow(self):
        backend = Meerk40tBackend()
        backend.start()
        try:
            project.create_project(backend, name="LaserJob")
            elements.add_circle(backend, "1in", "1in", "1in")
            elements.add_rect(backend, "0.5in", "0.5in", "1in", "1in")
            elements.add_text(backend, "1in", "1in", "Hello Laser")
            operations.classify_elements(backend)

            path = self.temp_path("workflow.svg")
            export.export_svg(backend, path)
            self.assertTrue(os.path.exists(path))
            self.assertGreater(os.path.getsize(path), 1000)
            root = ET.parse(path).getroot()
            self.assertTrue(root.tag.endswith("svg"))
            print(f"[workflow] exported SVG: {path} ({os.path.getsize(path)} bytes)")
        finally:
            backend.shutdown()


if __name__ == "__main__":
    unittest.main()
