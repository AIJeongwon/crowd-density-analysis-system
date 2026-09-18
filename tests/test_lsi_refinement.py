from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HAS_IMAGES = all(importlib.util.find_spec(name) is not None for name in ("numpy", "PIL"))


@unittest.skipUnless(HAS_IMAGES, "학습 자료 테스트에 NumPy와 Pillow가 필요합니다.")
class LsiRefinementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "training"))

    def test_contrast_preserves_shape_order_and_uint8(self):
        import numpy as np
        from prepare_lsi_refinement import reduce_contrast
        image = np.array([[0, 64, 128, 255]], dtype=np.uint8)
        changed = reduce_contrast(image, 0.5)
        self.assertEqual(changed.shape, image.shape)
        self.assertEqual(changed.dtype, np.uint8)
        self.assertTrue(np.all(np.diff(changed.astype(int)) >= 0))
        self.assertLess(int(changed.max()) - int(changed.min()), 255)
        np.testing.assert_array_equal(reduce_contrast(image, 1.0), image)

    def test_contrast_rejects_invalid_inputs(self):
        import numpy as np
        from prepare_lsi_refinement import reduce_contrast
        for factor in (0, -1, 1.1, float("nan")):
            with self.subTest(factor=factor), self.assertRaises(ValueError):
                reduce_contrast(np.zeros((2, 2), dtype=np.uint8), factor)
        with self.assertRaises(ValueError):
            reduce_contrast(np.zeros((2, 2), dtype=np.uint16), 0.5)

    def test_validation_and_test_never_receive_training_variants(self):
        from prepare_lsi_refinement import variants_for
        for split in ("val", "test"):
            self.assertEqual(variants_for(split, [15], [], 0), [])
            self.assertEqual(variants_for(split, [], [[0, 0, 10, 10, 0.9, 0]], 0), [])

    def test_positive_image_is_not_mined_as_empty_background(self):
        from prepare_lsi_refinement import variants_for
        self.assertEqual(variants_for("train", [50], [[0, 0, 10, 10, 0.9, 0]], 1), [])

    def test_mining_rejects_evaluation_split_and_foreign_identifiers(self):
        from prepare_lsi_refinement import validate_mining
        manifest = {"images": [{"id": "train_01_00001", "split": "train"}]}
        metadata = {"split": "train", "input_shape": [1, 3, 160, 160], "images": 1}
        validate_mining(metadata, manifest, {})
        with self.assertRaises(ValueError):
            validate_mining({**metadata, "split": "test"}, manifest, {})
        with self.assertRaises(ValueError):
            validate_mining(metadata, manifest, {"test_01_00001": []})

    def test_preparation_preserves_labels_and_evaluation_images(self):
        import numpy as np
        from PIL import Image
        from prepare_lsi_refinement import prepare
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, mining, output = root / "source", root / "mining", root / "output"
            mining.mkdir()
            records = []
            for split, identifier, label in [
                ("train", "train_01_00001", "0 0.5 0.5 0.5 0.75\n"),
                ("train", "train_01_00002", ""),
                ("val", "train_02_00001", "0 0.5 0.5 0.5 0.75\n"),
                ("test", "test_01_00001", ""),
            ]:
                for kind in ("images", "labels"):
                    (source / kind / split).mkdir(parents=True, exist_ok=True)
                Image.fromarray(np.arange(400, dtype=np.uint8).reshape(20, 20)).save(
                    source / "images" / split / f"{identifier}.png")
                (source / "labels" / split / f"{identifier}.txt").write_text(label, encoding="utf-8")
                records.append({"id": identifier, "source": identifier, "split": split,
                                "width": 20, "height": 20})
            (source / "manifest.json").write_text(json.dumps({"images": records}), encoding="utf-8")
            (mining / "metrics.json").write_text(json.dumps({"split": "train", "images": 2,
                "input_shape": [1, 3, 160, 160], "weights_sha256": "baseline"}), encoding="utf-8")
            (mining / "predictions.json").write_text(json.dumps({"train_01_00002": [
                [0, 0, 10, 10, 0.9, 0]]}), encoding="utf-8")
            report = prepare(source, mining, output)
            self.assertEqual(report["splits"]["train"]["images"], 8)
            self.assertEqual(report["splits"]["val"]["images"], 1)
            self.assertEqual(report["splits"]["test"]["images"], 1)
            for row in report["images"]:
                split, original, target = row["split"], row["source_id"], row["id"]
                self.assertEqual((source / "labels" / split / f"{original}.txt").read_bytes(),
                                 (output / "labels" / split / f"{target}.txt").read_bytes())
                if split != "train":
                    self.assertEqual((source / "images" / split / f"{original}.png").read_bytes(),
                                     (output / "images" / split / f"{target}.png").read_bytes())
            with self.assertRaises(ValueError):
                prepare(source, mining, output)


if __name__ == "__main__":
    unittest.main()
