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

    def test_uses_adaptive_display_range_by_default(self) -> None:
        args = self.camera.build_parser().parse_args([])

        self.assertIsNone(args.anchor_min_temp)
        self.assertIsNone(args.anchor_max_temp)

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

    def test_expands_narrow_temperature_range_around_center(self) -> None:
        self.assertEqual(
            self.camera.expand_temperature_range(24.0, 25.0, 2.0),
            (23.5, 25.5),
        )
        self.assertEqual(
            self.camera.expand_temperature_range(20.0, 30.0, 2.0),
            (20.0, 30.0),
        )

    def test_applies_absolute_temperature_anchor(self) -> None:
        self.assertEqual(
            self.camera.apply_temperature_anchor(
                (27.0, 33.0),
                (20.0, 40.0),
            ),
            (20.0, 40.0),
        )
        self.assertEqual(
            self.camera.apply_temperature_anchor(
                (5.0, 55.0),
                (20.0, 40.0),
            ),
            (5.0, 55.0),
        )

    def test_selects_y16_image_resolution_without_telemetry_rows(self) -> None:
        output = """
        [0]: 'UYVY' (UYVY 4:2:2)
            Size: Discrete 80x60
        [1]: 'Y16 ' (16-bit Greyscale)
            Size: Discrete 80x60
            Size: Discrete 80x63
        [2]: 'GREY' (8-bit Greyscale)
            Size: Discrete 80x60
        """

        resolutions = self.camera.parse_y16_resolutions(output)

        self.assertEqual(resolutions, [(80, 60), (80, 63)])
        self.assertEqual(
            self.camera.select_y16_resolution(resolutions),
            (80, 60),
        )

    def test_prefers_largest_native_y16_resolution(self) -> None:
        self.assertEqual(
            self.camera.select_y16_resolution(
                [(80, 60), (160, 120), (160, 123)]
            ),
            (160, 120),
        )

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

    @unittest.skipIf(np is None, "NumPy가 설치되지 않았습니다.")
    def test_temporal_filter_reduces_noise_without_delaying_motion(self) -> None:
        previous = np.full((3, 3), 20.0, dtype=np.float32)
        current = np.full((3, 3), 20.4, dtype=np.float32)
        current[1, 1] = 25.0

        filtered = self.camera.filter_display_temperature(
            current,
            previous,
            alpha=0.25,
            motion_threshold=1.0,
            np=np,
        )

        self.assertAlmostEqual(float(filtered[0, 0]), 20.1, places=4)
        self.assertAlmostEqual(float(filtered[1, 1]), 25.0, places=4)

    def test_detects_closed_window(self) -> None:
        class FakeCv2:
            WND_PROP_VISIBLE = 1
            error = RuntimeError

            @staticmethod
            def getWindowProperty(_window_name: str, _property: int) -> float:
                return 0.0

        self.assertFalse(
            self.camera.is_window_visible(FakeCv2, "CDAS Thermal Camera")
        )

    def test_treats_destroyed_window_as_closed(self) -> None:
        class FakeCv2:
            WND_PROP_VISIBLE = 1
            error = RuntimeError

            @staticmethod
            def getWindowProperty(_window_name: str, _property: int) -> float:
                raise RuntimeError("창이 이미 제거됨")

        self.assertFalse(
            self.camera.is_window_visible(FakeCv2, "CDAS Thermal Camera")
        )


if __name__ == "__main__":
    unittest.main()
