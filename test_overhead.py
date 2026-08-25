import tempfile
import unittest
from pathlib import Path

from monitor import operator_code, safe_svg
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

    def test_svg_safety_filter(self):
        self.assertIsNotNone(safe_svg(b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'))
        self.assertIsNone(safe_svg(b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>'))


if __name__ == "__main__":
    unittest.main()
