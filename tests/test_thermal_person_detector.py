from __future__ import annotations

import unittest
from pathlib import Path

from sensor_client_import import load_thermal_person_detector

try:
    import numpy as np
except ModuleNotFoundError:
    np = None


class ThermalPersonDetectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.detector = load_thermal_person_detector()

    def test_accepts_reduced_model_input(self) -> None:
        self.assertEqual(self.detector.validate_input_size(320), 320)

    def test_uses_640_input_by_default(self) -> None:
        args = self.detector.build_parser().parse_args(
            ["--model", "model.onnx"]
        )

        self.assertEqual(args.input_size, 640)

    def test_uses_synchronized_display_by_default(self) -> None:
        args = self.detector.build_parser().parse_args(
            ["--model", "model.onnx"]
        )

        self.assertEqual(args.display_mode, "synchronized")

    def test_limits_inference_to_four_fps_by_default(self) -> None:
        args = self.detector.build_parser().parse_args(
            ["--model", "model.onnx"]
        )

        self.assertEqual(args.inference_fps, 4.0)

    def test_rejects_excessive_inference_fps(self) -> None:
        with self.assertRaises(ValueError):
            self.detector.validate_inference_fps(31.0)

    def test_synchronized_display_uses_detection_source_frame(self) -> None:
        current_image = object()
        source_image = object()
        result = self.detector.DetectionResult(
            detections=(),
            inference_ms=400.0,
            source_image=source_image,
        )

        selected = self.detector.select_display_source(
            current_image,
            result,
            "synchronized",
        )

        self.assertIs(selected, source_image)

    def test_live_display_uses_current_frame(self) -> None:
        current_image = object()
        result = self.detector.DetectionResult(
            detections=(),
            inference_ms=400.0,
            source_image=object(),
        )

        selected = self.detector.select_display_source(
            current_image,
            result,
            "live",
        )

        self.assertIs(selected, current_image)

    def test_rejects_input_size_that_is_not_multiple_of_stride(self) -> None:
        with self.assertRaises(ValueError):
            self.detector.validate_input_size(300)

    def test_rejects_pytorch_model_path(self) -> None:
        with self.assertRaises(ValueError):
            self.detector.validate_model_path(Path("model.pt"))

    def test_restores_box_after_letterbox_padding(self) -> None:
        transform = self.detector.LetterboxTransform(
            scale=4.0,
            padding_x=0.0,
            padding_y=40.0,
        )

        restored = self.detector.restore_box(
            (40.0, 80.0, 160.0, 160.0),
            transform,
            (80, 60),
        )

        self.assertEqual(restored, (10, 10, 50, 50))

    def test_flattens_opencv_nms_indices(self) -> None:
        self.assertEqual(
            self.detector.flatten_nms_indices([[2], [5]]),
            [2, 5],
        )

    @unittest.skipIf(np is None, "NumPy가 설치되지 않았습니다.")
    def test_normalizes_uniform_temperature_frame(self) -> None:
        temperature = np.full((60, 80), 25.0, dtype=np.float32)

        normalized = self.detector.normalize_thermal_frame(
            temperature,
            minimum_span=2.0,
            np=np,
        )

        self.assertEqual(normalized.shape, (60, 80, 3))
        self.assertTrue(np.all(normalized == 127))

    @unittest.skipIf(np is None, "NumPy가 설치되지 않았습니다.")
    def test_extracts_person_confidence_from_yolov5_output(self) -> None:
        output = np.array(
            [[[160.0, 160.0, 80.0, 120.0, 0.8, 0.75]]],
            dtype=np.float32,
        )

        candidates = self.detector.extract_person_candidates(
            output,
            confidence_threshold=0.5,
            np=np,
        )

        self.assertEqual(len(candidates), 1)
        self.assertAlmostEqual(candidates[0].confidence, 0.6, places=5)
        self.assertEqual(candidates[0].box, (120.0, 100.0, 80.0, 120.0))

    @unittest.skipIf(np is None, "NumPy가 설치되지 않았습니다.")
    def test_rejects_candidate_below_combined_confidence(self) -> None:
        output = np.array(
            [[[160.0, 160.0, 80.0, 120.0, 0.7, 0.6]]],
            dtype=np.float32,
        )

        candidates = self.detector.extract_person_candidates(
            output,
            confidence_threshold=0.5,
            np=np,
        )

        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
