"""Unit tests for the strict manifest validator and JSON loader.

Pure dict/string driven; no Meerk40t kernel is required.
"""

from __future__ import annotations

import json
import unittest

from cli_anything.meerk40t.utils.manifest import (
    ManifestValidationError,
    strict_load_json,
    validate_manifest,
)


def _valid_job_manifest() -> dict:
    """Build a VALID job manifest matching job_prep's prepare_job shape."""
    return {
        "schema": "clia-job-manifest-v1",
        "kind": "job",
        "created": "2026-07-14T00:00:00+00:00",
        "machine": "mk-test",
        "material": "acrylic-3mm",
        "files": {
            "input_svg": {"path": "/tmp/in.svg", "sha256": "a" * 64},
            "job_svg": {"path": "/tmp/job.svg", "sha256": "b" * 64},
            "gcode": {"path": "/tmp/out.gcode", "sha256": "c" * 64},
        },
        "operations": [
            {
                "role": "outline",
                "kind": "cut",
                "color": "#ff0000",
                "passes": 1,
                "power": 500,
                "speed": 120.0,
                "elements": 4,
            },
            {
                "role": "detail",
                "kind": "engrave",
                "color": "#000000",
                "passes": 2,
                "power": 300,
                "speed": 250.0,
                "elements": 0,
            },
        ],
        "estimated_roles": ["outline", "detail"],
        "settings_fingerprint": "deadbeef" * 8,
        "verification": {
            "all_passed": True,
            "header_ok": True,
            "travel_s0_ok": True,
            "burn_s_ok": True,
            "end_ok": True,
            "in_bounds": True,
            "no_unassigned": True,
            "s_values": [1, 500, 300],
            "g1_s_values": [500, 300],
            "x_range": [0.0, 100.0],
            "y_range": [0.0, 50.0],
            "unassigned_elements": [],
            "extra_diagnostic": True,
        },
    }


def _valid_ladder_manifest() -> dict:
    m = _valid_job_manifest()
    m["kind"] = "ladder"
    m["role"] = "calibration"
    m["powers"] = [100, 200, 300]
    m["settings_fingerprint"] = None
    return m


class TestStrictLoadJson(unittest.TestCase):
    def test_parses_valid_job_manifest(self):
        text = json.dumps(_valid_job_manifest())
        parsed = strict_load_json(text)
        self.assertIsInstance(parsed, dict)
        self.assertEqual(parsed["schema"], "clia-job-manifest-v1")

    def test_rejects_nan(self):
        for bad in ("NaN", "Infinity", "-Infinity"):
            with self.assertRaises(ManifestValidationError):
                strict_load_json(bad)

    def test_rejects_nan_nested(self):
        with self.assertRaises(ManifestValidationError):
            strict_load_json('{"speed": NaN}')

    def test_rejects_duplicate_keys(self):
        with self.assertRaises(ManifestValidationError):
            strict_load_json('{"a":1,"a":2}')

    def test_rejects_non_dict_root(self):
        with self.assertRaises(ManifestValidationError):
            strict_load_json("[1,2]")

    def test_rejects_invalid_json(self):
        with self.assertRaises(ManifestValidationError):
            strict_load_json("{not valid")


class TestValidateJobManifest(unittest.TestCase):
    def test_valid_job_manifest(self):
        validate_manifest(_valid_job_manifest())

    def test_strict_load_json_then_validate(self):
        text = json.dumps(_valid_job_manifest())
        validate_manifest(strict_load_json(text))


class TestValidateLadderManifest(unittest.TestCase):
    def test_valid_ladder_manifest(self):
        validate_manifest(_valid_ladder_manifest())


class TestValidateRejections(unittest.TestCase):
    def _assert_error(self, manifest, field_substr):
        with self.assertRaises(ManifestValidationError) as ctx:
            validate_manifest(manifest)
        exc = ctx.exception
        self.assertIsInstance(exc, ManifestValidationError)
        self.assertTrue(len(exc.errors) > 0)
        self.assertTrue(
            any(field_substr in e for e in exc.errors),
            msg=f"expected field {field_substr!r} in errors: {exc.errors}",
        )

    def test_missing_files(self):
        m = _valid_job_manifest()
        del m["files"]
        self._assert_error(m, "files")

    def test_missing_verification(self):
        m = _valid_job_manifest()
        del m["verification"]
        self._assert_error(m, "verification")

    def test_missing_schema(self):
        m = _valid_job_manifest()
        del m["schema"]
        self._assert_error(m, "schema")

    def test_missing_kind(self):
        m = _valid_job_manifest()
        del m["kind"]
        self._assert_error(m, "kind")

    def test_unknown_top_level_key(self):
        m = _valid_job_manifest()
        m["bogus"] = 1
        self._assert_error(m, "unknown top-level key")

    def test_power_zero(self):
        m = _valid_job_manifest()
        m["operations"][0]["power"] = 0
        self._assert_error(m, "power")

    def test_power_over_max(self):
        m = _valid_job_manifest()
        m["operations"][0]["power"] = 1001
        self._assert_error(m, "power")

    def test_power_float(self):
        m = _valid_job_manifest()
        m["operations"][0]["power"] = 1.5
        self._assert_error(m, "power")

    def test_power_bool(self):
        m = _valid_job_manifest()
        m["operations"][0]["power"] = True
        self._assert_error(m, "power")

    def test_speed_non_positive(self):
        m = _valid_job_manifest()
        m["operations"][0]["speed"] = 0
        self._assert_error(m, "speed")

    def test_speed_non_finite(self):
        m = _valid_job_manifest()
        m["operations"][0]["speed"] = float("inf")
        self._assert_error(m, "speed")

    def test_passes_less_than_one(self):
        m = _valid_job_manifest()
        m["operations"][0]["passes"] = 0
        self._assert_error(m, "passes")

    def test_op_missing_role_for_job(self):
        m = _valid_job_manifest()
        del m["operations"][0]["role"]
        self._assert_error(m, "role")

    def test_invalid_kind(self):
        m = _valid_job_manifest()
        m["operations"][0]["kind"] = "score"
        self._assert_error(m, "kind")

    def test_settings_fingerprint_null_for_job(self):
        m = _valid_job_manifest()
        m["settings_fingerprint"] = None
        self._assert_error(m, "settings_fingerprint")

    def test_ladder_missing_powers(self):
        m = _valid_ladder_manifest()
        del m["powers"]
        self._assert_error(m, "powers")

    def test_ladder_missing_role(self):
        m = _valid_ladder_manifest()
        del m["role"]
        self._assert_error(m, "role")

    def test_verification_missing_all_passed(self):
        m = _valid_job_manifest()
        del m["verification"]["all_passed"]
        self._assert_error(m, "all_passed")

    def test_non_finite_in_x_range(self):
        m = _valid_job_manifest()
        m["verification"]["x_range"] = [0.0, float("nan")]
        self._assert_error(m, "x_range")


if __name__ == "__main__":
    unittest.main()
