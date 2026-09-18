from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

HAS_RUNTIME = all(importlib.util.find_spec(name) is not None
                  for name in ("torch", "torchvision", "numpy"))


@unittest.skipUnless(HAS_RUNTIME, "크기별 평가 테스트에 학습 환경이 필요합니다.")
class LsiMetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "training"))
        from evaluate_lsi import match_at_threshold
        cls.match = staticmethod(match_at_threshold)

    def test_empty_frame_predictions_are_false_positives(self):
        import numpy as np
        detections = np.array([[0, 0, 10, 20, 0.8, 0]], dtype=np.float32)
        result = self.match(detections, np.empty((0, 4), dtype=np.float32))
        self.assertEqual(result["false_positives"], 1)
        self.assertEqual(result["true_positives"], 0)

    def test_duplicate_detections_cannot_match_the_same_person_twice(self):
        import numpy as np
        detections = np.array([[0, 0, 10, 20, 0.8, 0], [0, 0, 10, 20, 0.7, 0]], dtype=np.float32)
        labels = np.array([[0, 0, 10, 20]], dtype=np.float32)
        result = self.match(detections, labels)
        self.assertEqual(result["true_positives"], 1)
        self.assertEqual(result["false_positives"], 1)
        self.assertEqual(result["height_bins"]["20_39"]["detected"], 1)

    def test_low_confidence_prediction_does_not_hide_a_miss(self):
        import numpy as np
        detections = np.array([[0, 0, 10, 15, 0.1, 0]], dtype=np.float32)
        result = self.match(detections, np.array([[0, 0, 10, 15]], dtype=np.float32))
        self.assertEqual(result["false_negatives"], 1)
        self.assertEqual(result["height_bins"]["10_19"]["total"], 1)

    def test_roundoff_does_not_change_height_bin(self):
        import numpy as np
        result = self.match(np.empty((0, 6), dtype=np.float32),
                            np.array([[0, 0, 10, 19.999998]], dtype=np.float32))
        self.assertEqual(result["height_bins"]["20_39"]["total"], 1)

    def test_negative_frame_rate_counts_frames_not_boxes(self):
        import numpy as np
        from evaluate_lsi import summarize_predictions
        empty = np.empty((0, 4), dtype=np.float32)
        detections = np.array([[0, 0, 10, 20, 0.8, 0], [20, 0, 30, 20, 0.7, 0]], dtype=np.float32)
        frames = [(detections, empty), (np.empty((0, 6), dtype=np.float32), empty)]
        result = summarize_predictions(frames, 0.25)
        self.assertEqual(result["false_positives"], 2)
        self.assertEqual(result["negative_frames_with_detections"], 1)
        self.assertEqual(result["negative_frame_false_positive_rate"], 0.5)

    def test_negative_frame_rate_without_negative_examples_is_unavailable(self):
        import numpy as np
        from evaluate_lsi import summarize_predictions
        result = summarize_predictions([(np.empty((0, 6), dtype=np.float32),
                                         np.array([[0, 0, 10, 20]], dtype=np.float32))], 0.25)
        self.assertIsNone(result["negative_frame_false_positive_rate"])

    def test_count_error_is_separate_from_box_localization_error(self):
        import numpy as np
        from evaluate_lsi import summarize_predictions
        result = summarize_predictions([(
            np.array([[30, 30, 40, 50, 0.8, 0]], dtype=np.float32),
            np.array([[0, 0, 10, 20]], dtype=np.float32))], 0.75)
        self.assertEqual(result["false_positives"], 1)
        self.assertEqual(result["false_negatives"], 1)
        self.assertEqual(result["count_mae"], 0.0)

    def test_count_absolute_error_does_not_cancel_over_and_under_counting(self):
        import numpy as np
        from evaluate_lsi import summarize_predictions
        result = summarize_predictions([
            (np.array([[0, 0, 10, 20, 0.8, 0]], dtype=np.float32), np.empty((0, 4), dtype=np.float32)),
            (np.empty((0, 6), dtype=np.float32), np.array([[0, 0, 10, 20]], dtype=np.float32)),
        ], 0.75)
        self.assertEqual(result["count_mean_signed_error"], 0.0)
        self.assertEqual(result["count_mae"], 1.0)

    def test_threshold_requires_precision_target_before_maximizing_recall(self):
        from calibrate_lsi import choose_threshold
        rows = [
            {"confidence": 0.25, "counts": {"precision": 0.8, "recall": 0.99, "true_positives": 99}},
            {"confidence": 0.75, "counts": {"precision": 0.96, "recall": 0.9, "true_positives": 90}},
            {"confidence": 0.85, "counts": {"precision": 0.99, "recall": 0.7, "true_positives": 70}},
        ]
        self.assertEqual(choose_threshold(rows, 0.95)["confidence"], 0.75)

    def test_threshold_does_not_treat_no_detections_as_perfect_precision(self):
        from calibrate_lsi import choose_threshold
        with self.assertRaises(ValueError):
            choose_threshold([{"confidence": 0.95, "counts": {
                "precision": None, "recall": 0.0, "true_positives": 0}}], 0.95)

    def test_calibration_cannot_cross_input_shapes(self):
        from calibrate_lsi import validate_policy
        policy = {"selected_on": "val", "weights_sha256": "same",
                  "input_shape": [1, 3, 160, 160], "selected": {"confidence": 0.75}}
        with self.assertRaises(ValueError):
            validate_policy(policy, {"weights_sha256": "same", "input_shape": [1, 3, 320, 320]})

    def test_legacy_calibration_is_only_accepted_for_160_square(self):
        from calibrate_lsi import validate_policy
        policy = {"selected_on": "val", "weights_sha256": "same", "selected": {"confidence": 0.75}}
        self.assertEqual(validate_policy(policy, {"weights_sha256": "same",
                                                  "input_shape": [1, 3, 160, 160]}), 0.75)
        with self.assertRaises(ValueError):
            validate_policy(policy, {"weights_sha256": "same", "input_shape": [1, 3, 128, 160]})

    def test_benchmark_shape_uses_height_then_width(self):
        from benchmark_lsi import parse_shape
        self.assertEqual(parse_shape("128x160"), (128, 160))

    def test_benchmark_shape_rejects_invalid_stride_and_dimensions(self):
        import argparse
        from benchmark_lsi import parse_shape
        for value in ("120x160", "0x160", "160", "160x160x160", "-32x160"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                parse_shape(value)

    def test_comparison_rejects_different_validation_and_test_shapes(self):
        import json
        import tempfile
        from compare_lsi import compare_pair
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for split, size in (("val", 160), ("test", 320)):
                (root / split).mkdir()
                (root / split / "metrics.json").write_text(json.dumps({
                    "split": split, "weights_sha256": "same", "input_shape": [1, 3, size, size]}),
                    encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "input_shape"):
                compare_pair(root / "val", root / "test", root)


if __name__ == "__main__":
    unittest.main()
