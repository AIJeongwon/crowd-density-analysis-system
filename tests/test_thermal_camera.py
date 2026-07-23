from __future__ import annotations

import unittest

from sensor_client_import import load_thermal_camera

try:
    import numpy as np
except ModuleNotFoundError:
    np = None


class ThermalCameraTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.camera = load_thermal_camera()

    def test_converts_centikelvin_to_celsius(self) -> None:
        self.assertAlmostEqual(
            self.camera.centikelvin_to_celsius(29815),
            25.0,
        )

    def test_accepts_complete_fixed_temperature_range(self) -> None:
        self.assertEqual(
            self.camera.validate_temperature_range(20.0, 40.0),
            (20.0, 40.0),
        )

    def test_rejects_incomplete_fixed_temperature_range(self) -> None:
        with self.assertRaises(ValueError):
            self.camera.validate_temperature_range(20.0, None)

    def test_rejects_reversed_fixed_temperature_range(self) -> None:
        with self.assertRaises(ValueError):
            self.camera.validate_temperature_range(40.0, 20.0)

    def test_validates_linux_video_device_path(self) -> None:
        self.assertEqual(
            self.camera.validate_device_path("/dev/video0"),
            "/dev/video0",
        )
        self.assertEqual(
            self.camera.validate_device_path(
                "/dev/v4l/by-id/usb-GroupGets_PureThermal-video-index0"
            ),
            "/dev/v4l/by-id/usb-GroupGets_PureThermal-video-index0",
        )

    def test_rejects_invalid_device_path(self) -> None:
        with self.assertRaises(ValueError):
            self.camera.validate_device_path("/dev/video0 ! fakesink")

    @unittest.skipIf(np is None, "NumPy가 설치되지 않았습니다.")
    def test_decodes_y16_byte_frame_and_calculates_statistics(self) -> None:
        raw = np.full((120, 160), 29815, dtype=np.uint16)
        raw[0, 0] = 29315
        raw[119, 159] = 30315
        packed = raw.astype("<u2").view(np.uint8).reshape(120, 320)

        decoded = self.camera.decode_y16_frame(packed, 160, 120, np)
        temperature = self.camera.raw_to_temperature(decoded, np)
        statistics = self.camera.calculate_statistics(temperature, None, np)

        np.testing.assert_array_equal(decoded, raw)
        self.assertEqual(statistics.center, 25.0)
        self.assertEqual(statistics.minimum, 20.0)
        self.assertEqual(statistics.maximum, 30.0)
        self.assertEqual(statistics.average, 25.0)


if __name__ == "__main__":
    unittest.main()
