import tempfile
import unittest
from pathlib import Path

from monitor import RADAR_RANGES, aircraft_identity, aircraft_name, operator_code, radar_contacts, safe_svg
from overhead import DEMO, HEIGHT, WIDTH, Settings, produce_frame, select_nearest


class OverHeadTests(unittest.TestCase):
    def test_selects_positioned_aircraft(self):
        plane, distance = select_nearest(DEMO["ac"], Settings())
        self.assertEqual(plane["flight"].strip(), "BAW283")
        self.assertGreaterEqual(distance, 0)

    def test_outputs_exact_rgb888_frame_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                output_rgb=str(Path(tmp) / "frame.rgb"),
                output_png=str(Path(tmp) / "frame.png"),
            )
            image, status = produce_frame(settings, demo=True)
            self.assertEqual(image.size, (WIDTH, HEIGHT))
            self.assertEqual(status, "demo")
            self.assertEqual((Path(tmp) / "frame.rgb").stat().st_size, WIDTH * HEIGHT * 3)
            self.assertTrue((Path(tmp) / "frame.png").is_file())

    def test_operator_code_ignores_registration_callsigns(self):
        self.assertEqual(operator_code({"flight": "RYR6432", "r": "SP-RZX"}), "RYR")
        self.assertEqual(operator_code({"flight": "GBNZB", "r": "G-BNZB"}), "")

    def test_aircraft_identity_prefers_hex_code(self):
        plane = {"hex": "4ca259", "r": "EI-DHD", "flight": "RYR701"}
        self.assertEqual(aircraft_identity(plane), "4CA259")
        self.assertEqual(aircraft_identity({"r": "G-BNZB"}), "GBNZB")

    def test_expands_common_aircraft_models(self):
        self.assertEqual(aircraft_name("B738"), "BOEING 737-800")
        self.assertEqual(aircraft_name("A20N"), "AIRBUS A320NEO")
        self.assertEqual(aircraft_name("P28A"), "PIPER PA-28 CHEROKEE")

    def test_svg_safety_filter(self):
        self.assertIsNotNone(safe_svg(b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'))
        self.assertIsNone(safe_svg(b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>'))

    def test_radar_centres_aircraft_at_home(self):
        settings = Settings(latitude=51.5, longitude=-3.3, radius_nm=25)
        plane = {"hex": "abc123", "flight": "TST123", "lat": 51.5, "lon": -3.3, "seen_pos": 0}
        contacts = radar_contacts([plane], settings, plane)
        self.assertEqual((contacts[0]["x"], contacts[0]["y"]), (50.0, 50.0))
        self.assertTrue(contacts[0]["selected"])

    def test_radar_ranges_are_ordered_and_include_default(self):
        self.assertEqual(tuple(sorted(RADAR_RANGES)), RADAR_RANGES)
        self.assertIn(25, RADAR_RANGES)


if __name__ == "__main__":
    unittest.main()
