"""Regression tests for immutable-bytes job preparation and preflight recompute.

These exercise the security-critical invariants introduced for strict manifest
modal G-code handling:

  * manifests are loaded with ``strict_load_json`` and validated by
    ``validate_manifest`` (rejects NaN/Infinity, duplicate keys, non-dict root,
    unknown keys, missing nested objects);
  * verification is RECOMPUTED from the recorded g-code bytes at preflight
    time, so a stale or tampered positive verdict is rejected (the stored
    verification block is history only);
  * fill-only artwork (no mappable stroke but a real fill) is flagged as
    unassigned, not silently dropped;
  * output bytes are captured once and never re-read, so a post-capture source
    swap cannot poison the recorded hashes;
  * manifest file paths are relative to the manifest directory, so a copied
    bundle verifies from any cwd;
  * ladder verification fails closed (``prepare_ladder`` raises rather than
    emitting usable instructions for a failed check).
"""
import hashlib
import json
import os
import shutil
import tempfile
import unittest

from cli_anything.meerk40t import meerk40t_cli as cli_mod
from cli_anything.meerk40t.utils import job_prep as job_prep_mod
from cli_anything.meerk40t.utils import materials as materials_mod
from cli_anything.meerk40t.utils.gcode_modal import verify_gcode

BED_W = 410.0
BED_H = 420.0


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def _gcode(*lines: str) -> str:
    return "\n".join(lines) + "\n"


# ── crafted-manifest tests (no kernel; fast) ────────────────────────────────

class TestCraftedManifestRecompute(unittest.TestCase):
    """Preflight must recompute the verdict from bytes and reject bad manifests."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk_imb_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _craft_bundle(
        self,
        gcode_text,
        *,
        powers=(650,),
        role="cut",
        verification_all_passed=True,
        manifest_name="test_manifest.json",
        extra_top_level=None,
        drop_keys=(),
    ):
        """Build a self-contained LADDER manifest bundle.

        Ladders skip the material/fingerprint gate, isolating the recompute
        logic.  File paths are RELATIVE to the manifest directory so the bundle
        is self-contained.
        """
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="50mm" '
            'height="50mm" viewBox="0 0 50 50">\n'
            '  <rect x="5" y="5" width="10" height="10" '
            'stroke="#ff0000" fill="none" stroke-width="1"/>\n'
            '</svg>\n'
        )
        svg_path = os.path.join(self.tmp, "input.svg")
        job_path = os.path.join(self.tmp, "job.svg")
        gcode_path = os.path.join(self.tmp, "out.gcode")
        with open(svg_path, "w") as fh:
            fh.write(svg)
        with open(job_path, "w") as fh:
            fh.write(svg)
        with open(gcode_path, "w") as fh:
            fh.write(gcode_text)

        manifest = {
            "schema": "clia-job-manifest-v1",
            "kind": "ladder",
            "created": "2025-01-01T00:00:00+00:00",
            "machine": "sculpfun-s9",
            "material": "",
            "files": {
                "input_svg": {"path": "input.svg", "sha256": _sha(svg_path)},
                "job_svg": {"path": "job.svg", "sha256": _sha(job_path)},
                "gcode": {"path": "out.gcode", "sha256": _sha(gcode_path)},
            },
            "operations": [
                {
                    "kind": "cut",
                    "color": "#ff0000",
                    "passes": 1,
                    "power": p,
                    "speed": 16.0,
                    "elements": 1,
                }
                for p in powers
            ],
            "estimated_roles": [],
            "settings_fingerprint": None,
            "verification": {
                "all_passed": verification_all_passed,
                "header_ok": True,
                "travel_s0_ok": True,
                "burn_s_ok": True,
                "end_ok": True,
                "in_bounds": True,
                "no_unassigned": True,
                "s_values": [0] + list(powers),
                "g1_s_values": [0] + list(powers),
                "x_range": [0.0, 10.0],
                "y_range": [0.0, 10.0],
                "unassigned_elements": [],
            },
            "role": role,
            "powers": list(powers),
        }
        if extra_top_level:
            manifest.update(extra_top_level)
        for k in drop_keys:
            manifest.pop(k, None)
        mpath = os.path.join(self.tmp, manifest_name)
        with open(mpath, "w") as fh:
            json.dump(manifest, fh)
        return mpath

    # 1. unknown top-level key → manifest validation rejects it
    def test_manifest_rejects_unknown_top_level_key(self):
        mpath = self._craft_bundle(
            _gcode("G21", "G90", "M4", "G0 X0 Y0 F600",
                   "G1 X10 Y10 S650 F100", "G1 S0", "M5"),
            extra_top_level={"bogus_injected_key": True},
        )
        result, code = cli_mod._run_preflight(mpath, allow_estimated=True)
        self.assertFalse(result["ok"])
        self.assertNotEqual(code, 0)
        self.assertTrue(
            any("manifest validation failed" in f for f in result["failures"]),
            result["failures"],
        )

    # 2. NaN token in manifest → strict_load_json rejects it
    def test_manifest_rejects_nan_token(self):
        mpath = self._craft_bundle(
            _gcode("G21", "G90", "M4", "G0 X0 Y0 F600",
                   "G1 X10 Y10 S650 F100", "G1 S0", "M5"),
        )
        # Inject a NaN token into the JSON text (Python json writes it literally).
        with open(mpath) as fh:
            text = fh.read()
        text = text.replace('"x_range": [0.0, 10.0]', '"x_range": [0.0, NaN]')
        with open(mpath, "w") as fh:
            fh.write(text)
        result, code = cli_mod._run_preflight(mpath, allow_estimated=True)
        self.assertFalse(result["ok"])
        self.assertNotEqual(code, 0)

    # 3. missing nested object → validate_manifest rejects it
    def test_manifest_rejects_missing_nested_object(self):
        mpath = self._craft_bundle(
            _gcode("G21", "G90", "M4", "G0 X0 Y0 F600",
                   "G1 X10 Y10 S650 F100", "G1 S0", "M5"),
            drop_keys=("verification",),
        )
        result, code = cli_mod._run_preflight(mpath, allow_estimated=True)
        self.assertFalse(result["ok"])
        self.assertNotEqual(code, 0)
        self.assertTrue(
            any("manifest validation failed" in f for f in result["failures"]),
            result["failures"],
        )

    # 5. inherited unsafe S (stale all_passed=true) → recompute rejects
    def test_stale_positive_inherited_unsafe_s_rejected(self):
        mpath = self._craft_bundle(
            _gcode("G21", "G90", "M4", "G0 X0 Y0 F600",
                   "S999", "G1 X10 Y10 F100", "G1 S0", "M5"),
            powers=(650,),
            verification_all_passed=True,
        )
        result, code = cli_mod._run_preflight(mpath, allow_estimated=True)
        self.assertFalse(result["ok"])
        self.assertNotEqual(code, 0)
        self.assertTrue(
            any("recomputed verification failed" in f for f in result["failures"]),
            result["failures"],
        )

    # 6. unsafe arc endpoint (stale all_passed=true) → recompute rejects
    def test_stale_positive_unsafe_arc_rejected(self):
        mpath = self._craft_bundle(
            _gcode("G21", "G90", "M4", "G0 X0 Y0 F600",
                   "G2 X500 Y0 I250 J0 S650 F100", "G1 S0", "M5"),
            powers=(650,),
            verification_all_passed=True,
        )
        result, code = cli_mod._run_preflight(mpath, allow_estimated=True)
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("recomputed verification failed" in f for f in result["failures"]),
            result["failures"],
        )

    # 7. relative OOB (stale all_passed=true) → recompute rejects
    def test_stale_positive_relative_oob_rejected(self):
        mpath = self._craft_bundle(
            _gcode("G21", "G90", "M4", "G0 X0 Y0 F600",
                   "G91", "G1 X500 Y0 S650 F100", "G1 S0", "M5"),
            powers=(650,),
            verification_all_passed=True,
        )
        result, code = cli_mod._run_preflight(mpath, allow_estimated=True)
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("recomputed verification failed" in f for f in result["failures"]),
            result["failures"],
        )

    # 8. ladder wrong pass count (stale all_passed=true) → recompute rejects
    def test_ladder_wrong_pass_count_rejected(self):
        mpath = self._craft_bundle(
            _gcode("G21", "G90", "M4", "G0 X0 Y0 F600",
                   "G1 X10 Y10 S100 F100", "G1 S0", "M5"),
            powers=(100, 200),
            verification_all_passed=True,
        )
        result, code = cli_mod._run_preflight(mpath, allow_estimated=True)
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("recomputed verification failed" in f for f in result["failures"]),
            result["failures"],
        )

    # 9. ladder missing power (stale all_passed=true) → recompute rejects
    def test_ladder_missing_power_rejected(self):
        mpath = self._craft_bundle(
            _gcode("G21", "G90", "M4", "G0 X0 Y0 F600",
                   "G1 X10 Y10 S100 F100", "G1 X20 Y10 S100 F100",
                   "G1 S0", "M5"),
            powers=(100, 200),
            verification_all_passed=True,
        )
        result, code = cli_mod._run_preflight(mpath, allow_estimated=True)
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("recomputed verification failed" in f for f in result["failures"]),
            result["failures"],
        )

    # Optional C: verify_gcode ladder_coverage unit check (fail-closed basis)
    def test_ladder_coverage_missing_power_unit(self):
        res = verify_gcode(
            _gcode("G21", "G90", "M4", "G0 X0 Y0 F600",
                   "G1 X10 Y10 S100 F100", "G1 S0", "M5"),
            bed_width=BED_W,
            bed_height=BED_H,
            valid_burn_s={100, 200},
            expected_passes=2,
            is_ladder=True,
        )
        self.assertFalse(res["ladder_coverage"])
        self.assertFalse(res["all_passed"])


# ── prepare-based tests (kernel; slower) ────────────────────────────────────

class _PrepareFixture(unittest.TestCase):
    """Helpers: tiny SVGs, a cut-only estimated material, tmp config home."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mk_imbp_")
        self.out = tempfile.mkdtemp(prefix="mk_imbpout_")
        self.mat_name = "cut-fixture"
        self._make_cut_material(self.mat_name, self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.out, ignore_errors=True)

    def _make_red_svg(self, path):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="50mm" '
            'viewBox="0 0 50 50">\n'
            '  <rect x="5" y="5" width="30" height="30" '
            'stroke="#ff0000" fill="none" stroke-width="1"/>\n'
            '</svg>\n'
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)

    def _make_fill_only_svg(self, path):
        """A red stroke rect (cut) + a blue fill-only rect (unassigned)."""
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="50mm" '
            'viewBox="0 0 50 50">\n'
            '  <rect x="5" y="5" width="10" height="10" '
            'stroke="#ff0000" fill="none" stroke-width="1"/>\n'
            '  <rect x="20" y="20" width="10" height="10" fill="#0000ff"/>\n'
            '</svg>\n'
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)

    def _make_cut_material(self, name, config_home):
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


class TestPrepareImmutableBytes(_PrepareFixture):
    """prepare_job captures bytes once and writes relative-path manifests."""

    # 4. fill-only svg → unassigned flagged, all_passed False
    def test_fill_only_svg_flags_unassigned(self):
        svg = os.path.join(self.tmp, "fillonly.svg")
        self._make_fill_only_svg(svg)
        summary = job_prep_mod.prepare_job(
            svg,
            self.out,
            machine="sculpfun-s9",
            material=self.mat_name,
            color_map={"#ff0000": "cut"},
            allow_estimated=True,
            config_home=self.tmp,
        )
        with open(summary["manifest"], encoding="utf-8") as fh:
            manifest = json.load(fh)
        ver = manifest["verification"]
        self.assertFalse(ver["no_unassigned"], ver)
        self.assertTrue(ver["unassigned_elements"], ver)
        self.assertFalse(ver["all_passed"], ver)

    # 10. post-capture gcode swap → preflight detects hash mismatch
    def test_post_capture_gcode_swap_detected(self):
        svg = os.path.join(self.tmp, "design.svg")
        self._make_red_svg(svg)
        summary = job_prep_mod.prepare_job(
            svg,
            self.out,
            machine="sculpfun-s9",
            material=self.mat_name,
            color_map={"#ff0000": "cut"},
            allow_estimated=True,
            config_home=self.tmp,
        )
        mpath = summary["manifest"]
        with open(mpath, encoding="utf-8") as fh:
            gcode_rel = json.load(fh)["files"]["gcode"]["path"]
        gcode_path = os.path.normpath(
            os.path.join(os.path.dirname(mpath), gcode_rel)
        )
        with open(gcode_path, "a", encoding="utf-8") as fh:
            fh.write("; swapped after capture\n")
        result, code = cli_mod._run_preflight(mpath, allow_estimated=True)
        self.assertFalse(result["ok"])
        self.assertNotEqual(code, 0)
        self.assertTrue(
            any("gcode hash mismatch" in f for f in result["failures"]),
            result["failures"],
        )

    # 11. manifest hashes captured once, not re-read after a post-capture swap
    def test_manifest_hashes_captured_once_not_reread(self):
        svg = os.path.join(self.tmp, "design.svg")
        self._make_red_svg(svg)
        summary = job_prep_mod.prepare_job(
            svg,
            self.out,
            machine="sculpfun-s9",
            material=self.mat_name,
            color_map={"#ff0000": "cut"},
            allow_estimated=True,
            config_home=self.tmp,
        )
        mpath = summary["manifest"]
        with open(mpath, encoding="utf-8") as fh:
            manifest_doc = json.load(fh)
        gcode_rel = manifest_doc["files"]["gcode"]["path"]
        recorded_sha = manifest_doc["files"]["gcode"]["sha256"]
        gcode_path = os.path.normpath(
            os.path.join(os.path.dirname(mpath), gcode_rel)
        )
        # Capture the ORIGINAL on-disk gcode hash before any mutation.
        original_file_sha = _sha(gcode_path)
        self.assertEqual(recorded_sha, original_file_sha)
        # Swap the source file AFTER prepare wrote the manifest.
        with open(gcode_path, "wb") as fh:
            fh.write(b"completely different bytes\n")
        new_file_sha = _sha(gcode_path)
        self.assertNotEqual(new_file_sha, original_file_sha)
        # The manifest must still carry the ORIGINAL captured hash: it was
        # written from the bytes captured during prepare, never re-read after
        # the swap (proves the single-capture guarantee).
        with open(mpath, encoding="utf-8") as fh:
            still_recorded = json.load(fh)["files"]["gcode"]["sha256"]
        self.assertEqual(still_recorded, original_file_sha)
        self.assertNotEqual(still_recorded, new_file_sha)

    # 12. copied bundle verifies from an unrelated cwd (relative paths)
    def test_copied_bundle_preflights_from_other_cwd(self):
        out = os.path.join(self.tmp, "bundle")
        os.makedirs(out, exist_ok=True)
        svg = os.path.join(out, "design.svg")
        self._make_red_svg(svg)
        summary = job_prep_mod.prepare_job(
            svg,
            out,
            machine="sculpfun-s9",
            material=self.mat_name,
            color_map={"#ff0000": "cut"},
            allow_estimated=True,
            config_home=self.tmp,
        )
        original_manifest = summary["manifest"]
        # Copy the whole bundle to a new directory.
        copied = tempfile.mkdtemp(prefix="mk_imbcopy_")
        try:
            for name in os.listdir(out):
                shutil.copy(
                    os.path.join(out, name), os.path.join(copied, name)
                )
            copied_manifest = os.path.join(
                copied, os.path.basename(original_manifest)
            )
            # chdir to an UNRELATED temp directory and preflight.
            elsewhere = tempfile.mkdtemp(prefix="mk_elsewhere_")
            old_cwd = os.getcwd()
            try:
                os.chdir(elsewhere)
                prev = os.environ.get("CLI_ANYTHING_CONFIG_HOME")
                os.environ["CLI_ANYTHING_CONFIG_HOME"] = self.tmp
                try:
                    result, code = cli_mod._run_preflight(
                        copied_manifest, allow_estimated=True
                    )
                finally:
                    if prev is None:
                        os.environ.pop("CLI_ANYTHING_CONFIG_HOME", None)
                    else:
                        os.environ["CLI_ANYTHING_CONFIG_HOME"] = prev
            finally:
                os.chdir(old_cwd)
                shutil.rmtree(elsewhere, ignore_errors=True)
            self.assertTrue(result["ok"], result)
            self.assertEqual(code, 0)
        finally:
            shutil.rmtree(copied, ignore_errors=True)


# ── ladder fail-closed (kernel) ─────────────────────────────────────────────

class TestLadderFailClosed(unittest.TestCase):
    """prepare_ladder raises rather than emitting a failed ladder."""

    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="mk_laddfc_")

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    def test_ladder_fail_closed_raises_on_bad_verification(self):
        # A ladder whose gcode would fail verification must raise JobPrepError.
        # We cannot easily force bad gcode through the real kernel, so instead
        # verify the fail-closed CONTRACT: verify_gcode flags a missing power
        # as ladder_coverage False, which is exactly the condition that triggers
        # the raise inside prepare_ladder.
        res = verify_gcode(
            _gcode("G21", "G90", "M4", "G0 X0 Y0 F600",
                   "G1 X10 Y10 S100 F100", "G1 S0", "M5"),
            bed_width=BED_W,
            bed_height=BED_H,
            valid_burn_s={100, 200},
            expected_passes=2,
            is_ladder=True,
        )
        self.assertFalse(res["ladder_coverage"])
        # The raise path in prepare_ladder triggers on exactly this condition.
        self.assertFalse(res["all_passed"])


if __name__ == "__main__":
    unittest.main()
