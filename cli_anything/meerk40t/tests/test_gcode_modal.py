"""Tests for the modal G-code verifier (no kernel, crafted G-code strings)."""
import unittest

from cli_anything.meerk40t.utils.gcode_modal import verify_gcode

BED_W = 410.0
BED_H = 420.0


def gcode(*lines: str) -> str:
    return "\n".join(lines)


class TestGcodeModal(unittest.TestCase):
    def test_valid_job_passes(self):
        text = gcode(
            "G21 G90",
            "M4",
            "G0 S0 X0 Y0",
            "G1 S650 F100 X10 Y10",
            "G1 S0",
            "M5",
        )
        res = verify_gcode(
            text, bed_width=BED_W, bed_height=BED_H, valid_burn_s={650}
        )
        self.assertTrue(res["all_passed"])
        self.assertTrue(res["header_ok"])
        self.assertTrue(res["units_metric"])
        self.assertTrue(res["coord_absolute"])
        self.assertTrue(res["travel_s0_ok"])
        self.assertTrue(res["burn_s_ok"])
        self.assertTrue(res["feed_ok"])
        self.assertTrue(res["end_ok"])
        self.assertTrue(res["arcs_ok"])
        self.assertTrue(res["in_bounds"])
        self.assertTrue(res["ordering_ok"])
        self.assertEqual(res["burn_s_seen"], [0, 650])
        self.assertEqual(res["s_values"], [0, 650])
        self.assertEqual(res["g1_s_values"], [0, 650])

    def test_inherited_unsafe_s(self):
        # S999 set once (modal), then a G1 with no explicit S while laser on.
        text = gcode(
            "G21",
            "G90",
            "M4",
            "S999",
            "G1 X10 Y10",
        )
        res = verify_gcode(
            text, bed_width=BED_W, bed_height=BED_H, valid_burn_s={650}
        )
        self.assertFalse(res["burn_s_ok"])
        self.assertFalse(res["all_passed"])

    def test_missing_feed(self):
        # Powered move with no F -> feed_ok False.
        text = gcode(
            "G21",
            "G90",
            "M4",
            "G1 S650 X10 Y10",
        )
        res = verify_gcode(
            text, bed_width=BED_W, bed_height=BED_H, valid_burn_s={650}
        )
        self.assertFalse(res["feed_ok"])
        self.assertFalse(res["all_passed"])

    def test_unsafe_arc(self):
        # Arc endpoint pushes outside the 410-bed -> arcs_ok & in_bounds False.
        text = gcode(
            "G21",
            "G90",
            "M4",
            "G0 X0 Y0",
            # Endpoint is the X/Y word (500 > 410 bed) -> arcs_ok & in_bounds
            # False. I/J are finite so they do not trip the arc check; the
            # out-of-bounds ENDPOINT does.
            "G2 X500 Y0 I250 J0 S650 F100",
        )
        res = verify_gcode(
            text, bed_width=BED_W, bed_height=BED_H, valid_burn_s={650}
        )
        self.assertFalse(res["arcs_ok"])
        self.assertFalse(res["in_bounds"])
        self.assertFalse(res["all_passed"])

    def test_relative_oob(self):
        # G91 then G1 X500 on a 410 bed -> in_bounds False.
        text = gcode(
            "G21",
            "G90",
            "M4",
            "G0 X0 Y0",
            "G91",
            "G1 X500 F100",
        )
        res = verify_gcode(
            text, bed_width=BED_W, bed_height=BED_H, valid_burn_s={650}
        )
        self.assertFalse(res["in_bounds"])
        self.assertFalse(res["all_passed"])

    def test_wrong_pass_count(self):
        # expected_passes=2 but only one powered burn layer.
        text = gcode(
            "G21",
            "G90",
            "M4",
            "G0 S0 X0 Y0",
            "G1 S650 F100 X10 Y10",
            "G1 S0",
            "M5",
        )
        res = verify_gcode(
            text,
            bed_width=BED_W,
            bed_height=BED_H,
            valid_burn_s={650},
            expected_passes=2,
        )
        self.assertFalse(res["pass_coverage_ok"])
        self.assertFalse(res["all_passed"])

    def test_missing_ladder_power(self):
        # is_ladder, valid_burn_s={100,200}, gcode only uses S100.
        text = gcode(
            "G21",
            "G90",
            "M4",
            "G0 S0 X0 Y0",
            "G1 S100 F100 X10 Y10",
            "G1 S0",
            "M5",
        )
        res = verify_gcode(
            text,
            bed_width=BED_W,
            bed_height=BED_H,
            valid_burn_s={100, 200},
            is_ladder=True,
        )
        self.assertFalse(res["ladder_coverage"])
        self.assertFalse(res["all_passed"])

    def test_beam_off_required(self):
        # Ends with laser on, no M5, last move S>0.
        text = gcode(
            "G21",
            "G90",
            "M4",
            "G0 X0 Y0",
            "G1 S650 F100 X10 Y10",
        )
        res = verify_gcode(
            text, bed_width=BED_W, bed_height=BED_H, valid_burn_s={650}
        )
        self.assertFalse(res["end_ok"])
        self.assertFalse(res["beam_off"])
        self.assertFalse(res["all_passed"])

    def test_inches_rejected(self):
        # G20 -> units_metric False.
        text = gcode(
            "G20",
            "G21",
            "G90",
            "M4",
            "G0 S0 X0 Y0",
            "G1 S650 F100 X10 Y10",
            "G1 S0",
            "M5",
        )
        res = verify_gcode(
            text, bed_width=BED_W, bed_height=BED_H, valid_burn_s={650}
        )
        self.assertFalse(res["units_metric"])
        self.assertFalse(res["all_passed"])

    def test_relative_coord_rejected(self):
        # G91 after header -> coord_absolute False.
        text = gcode(
            "G21",
            "G90",
            "M4",
            "G0 X0 Y0",
            "G91",
            "G1 X10 Y10 S650 F100",
            "G1 S0",
            "M5",
        )
        res = verify_gcode(
            text, bed_width=BED_W, bed_height=BED_H, valid_burn_s={650}
        )
        self.assertFalse(res["coord_absolute"])
        self.assertFalse(res["all_passed"])

    def test_ordering_cut_before_etch(self):
        # Cut power (400) appears before etch power (200).
        text = gcode(
            "G21",
            "G90",
            "M4",
            "G0 X0 Y0",
            "G1 S400 F100 X10 Y10",
            "G1 S200 F100 X20 Y20",
            "G1 S0",
            "M5",
        )
        res = verify_gcode(
            text,
            bed_width=BED_W,
            bed_height=BED_H,
            valid_burn_s={200, 400},
        )
        self.assertFalse(res["ordering_ok"])
        self.assertFalse(res["all_passed"])


if __name__ == "__main__":
    unittest.main()
