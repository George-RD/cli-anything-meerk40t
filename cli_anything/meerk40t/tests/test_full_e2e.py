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
import glob
import re as _re
import socket

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



def _gcode_s_values(path):
    """Return the sorted unique S values found in a G-code file."""
    text = open(path, "r", encoding="utf-8", errors="replace").read()
    return sorted({int(m) for m in _re.findall(r"S(\d+)", text)})


def _first_last_burn_s(path):
    """Return (first_burn_s, last_burn_s) scanning G0/G1 lines in order."""
    text = open(path, "r", encoding="utf-8", errors="replace").read()
    burns = []
    for line in text.splitlines():
        if not (line.startswith("G0") or line.startswith("G1")):
            continue
        m = _re.search(r"S(\d+)", line)
        if m:
            burns.append(int(m.group(1)))
    nonzero = [s for s in burns if s > 0]
    return (nonzero[0] if nonzero else None, nonzero[-1] if nonzero else None)


class TestSmartLaserWorkflow(unittest.TestCase):
    """End-to-end subprocess coverage for the material/job/ladder workflow.

    These drive the installed CLI against the real Meerk40tBackend. No skips:
    the backend is a required dependency and the tests must fail if it is missing.
    """

    CLI_BASE = _resolve_cli("cli-anything-meerk40t")

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="mk_smart_")
        self.fixture = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fixture_3colour.svg"
        )
        self.red_only = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fixture_red_only.svg"
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run(self, args, env=None):
        cmd = self.CLI_BASE + args
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env=env,
        )

    def _json(self, args, env=None):
        result = self._run(["--json"] + args, env=env)
        self.assertEqual(
            result.returncode,
            0,
            f"Command failed: {args}\nstdout: {result.stdout}\nstderr: {result.stderr}",
        )
        self.assertTrue(result.stdout.strip(), f"No JSON for {args}")
        return json.loads(result.stdout)

    def _config_env(self):
        cfg = tempfile.mkdtemp(prefix="mk_cfg_")
        env = os.environ.copy()
        env["CLI_ANYTHING_CONFIG_HOME"] = cfg
        return env

    def test_materials_list_and_show(self):
        listing = self._json(["materials", "list"])
        names = [m["name"] for m in listing["materials"]]
        self.assertIn("kraft-350gsm", names)
        roles = self._json(
            ["materials", "show", "kraft-350gsm", "--machine", "sculpfun-s9"]
        )
        self.assertEqual(set(roles["roles"].keys()), {"cut", "score", "etch"})

    def test_job_prepare_gate(self):
        out = os.path.join(self.temp_dir, "gate")
        os.makedirs(out, exist_ok=True)
        args = [
            "--machine", "sculpfun-s9", "job", "prepare", self.fixture,
            "--out-dir", out, "--material", "kraft-350gsm",
        ]
        denied = self._run(["--json"] + args)
        self.assertEqual(denied.returncode, 2, denied.stdout + denied.stderr)
        payload = json.loads(denied.stdout)
        self.assertEqual(sorted(payload["estimated_roles"]), ["cut", "etch"])

        allowed = self._run(["--json"] + args + ["--allow-estimated"])
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        summary = json.loads(allowed.stdout)
        gcode = summary["gcode"]
        self.assertEqual(_gcode_s_values(gcode), [0, 280, 380, 650])
        first, last = _first_last_burn_s(gcode)
        self.assertEqual(first, 380)
        self.assertEqual(last, 650)
        self.assertTrue(os.path.exists(summary["manifest"]))

        # The allowed prepare passes the same-file preflight gate...
        pf = self._run(
            ["--json", "job", "preflight", summary["manifest"], "--allow-estimated"]
        )
        self.assertEqual(pf.returncode, 0, pf.stderr)
        # ...but refuses when estimated roles are not acknowledged.
        pf2 = self._run(["--json", "job", "preflight", summary["manifest"]])
        self.assertEqual(pf2.returncode, 2, pf2.stdout)

    def test_determinism_swap(self):
        env = self._config_env()
        self._json(
            ["--machine", "sculpfun-s9", "materials", "create", "swap-test",
             "--description", "swap material", "--machine", "sculpfun-s9"],
            env=env,
        )
        for role, power, speed in (
            ("cut", 700, 16.0),
            ("score", 300, 20.0),
            ("etch", 410, 40.0),
        ):
            self._json(
                ["--machine", "sculpfun-s9", "materials", "record", "swap-test",
                 "--machine", "sculpfun-s9", "--role", role, "--power", str(power),
                 "--speed", str(speed), "--passes", "1", "--provenance", "tested",
                 "--note", f"{role} test burn on scrap 2026-07-12 clean pass through"],
                env=env,
            )
        out = os.path.join(self.temp_dir, "swap")
        os.makedirs(out, exist_ok=True)
        summary = self._json(
            ["--machine", "sculpfun-s9", "job", "prepare", self.fixture,
             "--out-dir", out, "--material", "swap-test"],
            env=env,
        )
        self.assertEqual(_gcode_s_values(summary["gcode"]), [0, 300, 410, 700])

    def test_new_material_lifecycle(self):
        env = self._config_env()
        self._json(
            ["--machine", "sculpfun-s9", "materials", "create", "scrap-test",
             "--description", "x", "--machine", "sculpfun-s9"],
            env=env,
        )
        out = os.path.join(self.temp_dir, "life")
        os.makedirs(out, exist_ok=True)
        missing = self._run(
            ["--machine", "sculpfun-s9", "job", "prepare", self.fixture,
             "--out-dir", out, "--material", "scrap-test"],
            env=env,
        )
        self.assertEqual(missing.returncode, 1, missing.stdout + missing.stderr)
        self.assertIn("no 'cut' settings", missing.stdout + missing.stderr)

        lad = os.path.join(self.temp_dir, "lad")
        os.makedirs(lad, exist_ok=True)
        bad = self._run(
            ["--machine", "sculpfun-s9", "job", "ladder", "--out-dir", lad,
             "--role", "cut", "--powers", "0,1200", "--speed", "16"]
        )
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        self.assertIn("outside the valid range", bad.stdout + bad.stderr)
        empty = self._run(
            ["--machine", "sculpfun-s9", "job", "ladder", "--out-dir", lad,
             "--role", "cut", "--powers", "", "--speed", "16"]
        )
        self.assertEqual(empty.returncode, 1, empty.stdout + empty.stderr)
        good = self._run(
            ["--machine", "sculpfun-s9", "job", "ladder", "--out-dir", lad,
             "--role", "cut", "--powers", "550,650,750", "--speed", "16"]
        )
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
        gcode_file = glob.glob(os.path.join(lad, "*.gcode"))[0]
        self.assertEqual(_gcode_s_values(gcode_file), [0, 550, 650, 750])

        short = self._run(
            ["--machine", "sculpfun-s9", "materials", "record", "scrap-test",
             "--machine", "sculpfun-s9", "--role", "cut", "--power", "650",
             "--speed", "16", "--passes", "1", "--provenance", "tested",
             "--note", "short"],
            env=env,
        )
        self.assertEqual(short.returncode, 1, short.stdout + short.stderr)
        recorded = self._json(
            ["--machine", "sculpfun-s9", "materials", "record", "scrap-test",
             "--machine", "sculpfun-s9", "--role", "cut", "--power", "650",
             "--speed", "16", "--passes", "1", "--provenance", "tested",
             "--note", "cut test on scrap 2026-07-12 clean pass through"],
            env=env,
        )
        self.assertEqual(recorded["settings"]["provenance"], "tested")
        red = os.path.join(self.temp_dir, "red")
        os.makedirs(red, exist_ok=True)
        done = self._run(
            ["--machine", "sculpfun-s9", "job", "prepare", self.red_only,
             "--out-dir", red, "--material", "scrap-test", "--map", "#ff0000=cut"],
            env=env,
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_attach_ignores_global_project_and_skips_kernel(self):
        # A global --project must not boot or touch the local kernel for the
        # attach thin client: it fast-fails on a dead port with the no-frame
        # error rather than crashing on a None backend or paying kernel cost.
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("", 0))
        dead_port = probe.getsockname()[1]
        probe.close()  # nothing listens here now -> connection refused
        res = self._run(
            ["--json", "--project", self.fixture, "attach",
             "--port", str(dead_port), "status"]
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("no #CLIA1# frame", res.stdout + res.stderr)
        self.assertNotIn("Traceback", res.stdout + res.stderr)


class TestAttachRoundTrip(unittest.TestCase):
    """Attach commands drive a live headless kernel over the consoleserver.

    The kernel + consoleserver run in-process on a free ephemeral port (never
    2323). The CLI subprocess connects to it. Shutdown is clean (no hang):
    the console-server module is closed and the kernel is shut down in tearDown.
    """

    CLI_BASE = _resolve_cli("cli-anything-meerk40t")

    @staticmethod
    def _free_port():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="mk_attach_")
        self.fixture = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fixture_3colour.svg"
        )
        self.port = self._free_port()
        self.kernel = self._boot_kernel(self.port)
        self._prepare_job()

    def _boot_kernel(self, port):
        from meerk40t.kernel import Kernel
        from meerk40t.device import dummydevice
        import cli_anything.meerk40t.mk_plugin as mk
        from meerk40t.network import console_server
        from meerk40t.network.tcp_server import plugin as tcp_plugin

        k = Kernel("MeerK40t", "0.0.0", "attachtest", ansi=False, ignore_settings=True)
        k.add_plugin(dummydevice.plugin)
        k.add_plugin(mk.plugin)
        k.add_plugin(console_server.plugin)
        k.add_plugin(tcp_plugin)
        k(partial=True)
        server = k.root.open_as("module/TCPServer", "console-server", port=port)
        send = k.root.channel("console-server/send")
        send.greet = "cli-anything attach test console.\r\n"
        send.line_end = "\r\n"
        recv = k.root.channel("console-server/recv")
        console = k.root.channel("console")
        console.watch(send)
        server.events_channel.watch(console)

        def _exec(data):
            if isinstance(data, bytes):
                try:
                    data = data.decode()
                except UnicodeDecodeError:
                    return
            k.root.console(data)

        recv.watch(_exec)
        return k

    def _prepare_job(self):
        out = os.path.join(self.temp_dir, "job")
        os.makedirs(out, exist_ok=True)
        res = self._run(
            ["--machine", "sculpfun-s9", "--json", "job", "prepare",
             self.fixture, "--out-dir", out, "--material", "kraft-350gsm",
             "--allow-estimated"],
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        summary = json.loads(res.stdout)
        self.job_svg = summary["job_svg"]
        self.manifest = summary["manifest"]

    def _run(self, args):
        cmd = self.CLI_BASE + args
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def tearDown(self):
        try:
            self.kernel.root.close("console-server")
        except Exception:
            pass
        try:
            self.kernel.shutdown()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_attach_status_live(self):
        res = self._run(
            ["--machine", "sculpfun-s9", "--json", "attach",
             "--port", str(self.port), "status"]
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data["protocol"], 1)

    def test_attach_stage_live(self):
        res = self._run(
            ["--machine", "sculpfun-s9", "--json", "attach",
             "--port", str(self.port), "stage", "--allow-estimated",
             self.job_svg, self.manifest]
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data["staged"], self.job_svg)
        self.assertEqual(len(data["operations"]), 3)
        powers = sorted(op["power"] for op in data["operations"])
        self.assertEqual(powers, [280, 380, 650])

    def test_attach_status_closed(self):
        self.kernel.root.close("console-server")
        res = self._run(
            ["--machine", "sculpfun-s9", "--json", "attach",
             "--port", str(self.port), "status"]
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("no #CLIA1# frame", res.stdout + res.stderr)

if __name__ == "__main__":
    unittest.main()
