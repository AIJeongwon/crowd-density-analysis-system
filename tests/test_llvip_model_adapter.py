from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SENSOR_CLIENT_DIR = Path(__file__).resolve().parents[1] / "sensor-client"
if str(SENSOR_CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(SENSOR_CLIENT_DIR))

import llvip_model_adapter as adapter  # noqa: E402

try:
    import cv2
except ModuleNotFoundError:
    cv2 = None

try:
    import numpy as np
except ModuleNotFoundError:
    np = None


class LlvipModelAdapterTest(unittest.TestCase):
    def test_reads_fixed_square_model_input_size(self) -> None:
        self.assertEqual(adapter.resolve_input_size([1, 3, 160, 160]), 160)

    def test_uses_release_size_for_dynamic_model_input(self) -> None:
        self.assertEqual(
            adapter.resolve_input_size([1, 3, "height", "width"]),
            adapter.DEFAULT_INPUT_SIZE,
        )

    def test_rejects_non_square_model_input(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be square"):
            adapter.resolve_input_size([1, 3, 160, 320])

    def test_requires_onnx_model_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "model.pt"
            path.write_bytes(b"model")
            with self.assertRaisesRegex(ValueError, "ONNX"):
                adapter.validate_model_path(path)

    def test_flattens_opencv_nms_indices(self) -> None:
        self.assertEqual(adapter.flatten_nms_indices([[2], [5]]), [2, 5])
        self.assertEqual(adapter.flatten_nms_indices(None), [])

    @unittest.skipIf(np is None, "NumPy is not installed")
    def test_preserves_rotated_row_major_thermal_orientation(self) -> None:
        image = adapter.thermal_frame_to_image(
            {
                "thermal": {
                    "width": 2,
                    "height": 3,
                    "pixels": (
                        27315,
                        27415,
                        27515,
                        27615,
                        27715,
                        27815,
                    ),
                }
            },
            np,
        )

        self.assertEqual(image.shape, (3, 2, 3))
        grayscale = image[:, :, 0]
        self.assertTrue(grayscale[0, 0] < grayscale[0, 1])
        self.assertTrue(grayscale[0, 1] < grayscale[1, 0])
        self.assertTrue(np.array_equal(image[:, :, 0], image[:, :, 1]))
        self.assertTrue(np.array_equal(image[:, :, 1], image[:, :, 2]))

    @unittest.skipIf(np is None, "NumPy is not installed")
    def test_normalizes_uniform_temperature_frame(self) -> None:
        temperature = np.full((3, 2), 25.0, dtype=np.float32)

        image = adapter.normalize_thermal_frame(
            temperature,
            minimum_span=2.0,
            np=np,
        )

        self.assertEqual(image.shape, (3, 2, 3))
        self.assertTrue(np.all(image == 127))

    @unittest.skipIf(np is None, "NumPy is not installed")
    def test_extracts_only_person_candidates_above_threshold(self) -> None:
        output = np.array(
            [
                [
                    [80.0, 80.0, 40.0, 60.0, 0.8, 0.75, 0.10],
                    [80.0, 80.0, 40.0, 60.0, 0.9, 0.10, 0.80],
                ]
            ],
            dtype=np.float32,
        )

        candidates = adapter.extract_person_candidates(
            output,
            confidence_threshold=0.5,
            np=np,
        )

        self.assertEqual(len(candidates), 1)
        self.assertAlmostEqual(candidates[0].confidence, 0.6, places=5)


    def test_restores_box_after_letterbox_padding(self) -> None:
        transform = adapter.LetterboxTransform(
            scale=1.0,
            padding_x=20.0,
            padding_y=0.0,
        )

        restored = adapter.restore_box(
            (30.0, 20.0, 40.0, 60.0),
            transform,
            (120, 160),
        )

        self.assertEqual(restored, (10, 20, 50, 80))

    def test_debug_output_path_uses_fused_timestamp(self) -> None:
        timestamp = datetime(
            2026,
            9,
            2,
            8,
            30,
            15,
            123456,
            tzinfo=timezone.utc,
        )

        path = adapter.debug_output_path(
            {
                "fused_at": timestamp.isoformat(),
                "debug": {
                    "enabled": True,
                    "output_dir": "/tmp/cdas",
                },
            }
        )

        self.assertEqual(
            path,
            Path("/tmp/cdas/20260902T083015_123456Z_inference.png"),
        )

    def test_debug_disabled_skips_inference_image(self) -> None:
        path = adapter.save_debug_detection_image(
            {"debug": {"enabled": False}},
            object(),
            (),
            object(),
        )

        self.assertIsNone(path)

    @unittest.skipIf(
        np is None or cv2 is None,
        "NumPy or OpenCV is not installed",
    )
    def test_saves_thermal_inference_image_with_bounding_box(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            image = np.zeros((20, 30, 3), dtype=np.uint8)
            detection = adapter.Detection(
                box=(3, 2, 18, 15),
                confidence=0.9,
            )
            timestamp = datetime(
                2026,
                9,
                2,
                8,
                30,
                15,
                tzinfo=timezone.utc,
            )

            path = adapter.save_debug_detection_image(
                {
                    "fused_at": timestamp.isoformat(),
                    "debug": {
                        "enabled": True,
                        "output_dir": temporary_directory,
                    },
                },
                image,
                (detection,),
                cv2,
            )

            self.assertIsNotNone(path)
            assert path is not None
            self.assertTrue(path.is_file())
            saved = cv2.imread(str(path), cv2.IMREAD_COLOR)
            self.assertIsNotNone(saved)
            self.assertEqual(saved.shape, (20, 30, 3))
            self.assertEqual(saved[2, 3].tolist(), [0, 255, 0])


if __name__ == "__main__":
    unittest.main()
