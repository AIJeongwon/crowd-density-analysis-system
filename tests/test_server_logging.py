from __future__ import annotations

import io
import struct
import tempfile
import unittest

raise unittest.SkipTest("server-side raw thermal image logging was retired")

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from backend.app.models import SensorReading
from backend.app.server import build_argument_parser, process_received_reading


class ServerLoggingTest(unittest.TestCase):
    def make_reading(self) -> SensorReading:
        now = datetime(2026, 8, 1, 6, 30, tzinfo=timezone.utc)
        return SensorReading(
            device_id="raspberry/pi lepton",
            location_id="test-location",
            sensor_type="thermal",
            timestamp=now,
            metrics={
                "valid": True,
                "width": 2,
                "height": 2,
                "pixels": [27315, 28000, 29000, 30000],
            },
            received_at=now,
        )

    def test_normal_mode_prints_summary_without_saving_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            log_dir = Path(temporary_directory) / ".log_data"
            output = io.StringIO()
            with redirect_stdout(output):
                result = process_received_reading(
                    self.make_reading(),
                    logging_enabled=False,
                    log_dir=log_dir,
                )

            self.assertIsNone(result)
            self.assertIn("received sensor reading", output.getvalue())
            self.assertIn("device_id=raspberry/pi lepton", output.getvalue())
            self.assertFalse(log_dir.exists())

    def test_logging_mode_saves_thermal_color_png(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            log_dir = Path(temporary_directory) / ".log_data"
            output = io.StringIO()
            with redirect_stdout(output):
                image_path = process_received_reading(
                    self.make_reading(),
                    logging_enabled=True,
                    log_dir=log_dir,
                )

            self.assertIsNotNone(image_path)
            assert image_path is not None
            image_data = image_path.read_bytes()
            self.assertEqual(image_data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", image_data[16:24]), (2, 2))
            self.assertEqual(image_data[24], 8)
            self.assertEqual(image_data[25], 2)
            self.assertEqual(image_path.parent, log_dir)
            self.assertIn("raspberry_pi_lepton", image_path.name)
            self.assertIn("saved thermal image", output.getvalue())

    def test_logging_is_a_boolean_option(self) -> None:
        parser = build_argument_parser()
        self.assertFalse(parser.parse_args([]).logging)
        self.assertTrue(parser.parse_args(["--logging"]).logging)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["logging"])


if __name__ == "__main__":
    unittest.main()
