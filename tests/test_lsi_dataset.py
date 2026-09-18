from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
    from training import prepare_lsi as lsi
except ModuleNotFoundError:
    lsi = None


@unittest.skipIf(lsi is None, "LSI 변환 테스트에 NumPy와 Pillow가 필요합니다.")
class LsiDatasetTest(unittest.TestCase):
    def annotation(self, name: str, box=(2, 2, 6, 7)) -> str:
        return (
            f'Image filename : "{name}"\n'
            'Image size (X x Y x C) : 8 x 10 x 1\n'
            'Objects with ground truth : 1 { "PASperson" }\n'
            'Bounding box for object 1 "PASperson" (Xmin, Ymin) - (Xmax, Ymax) : '
            f'({box[0]}, {box[1]}) - ({box[2]}, {box[3]})\n'
        )

    def test_reads_pascal_text_not_xml(self):
        name, boxes = lsi.parse_annotation(self.annotation("Train/01/00001.png"))
        self.assertEqual(name, "Train/01/00001.png")
        self.assertEqual(boxes, [(2, 2, 6, 7)])

    def test_rejects_missing_box(self):
        text = self.annotation("Train/01/00001.png").replace("truth : 1", "truth : 2")
        with self.assertRaises(ValueError):
            lsi.parse_annotation(text)

    def test_rejects_parent_path(self):
        with self.assertRaises(ValueError):
            lsi.parse_annotation(self.annotation("../00001.png"))

    def test_uses_actual_image_dimensions_and_inclusive_coordinates(self):
        self.assertEqual(lsi.to_yolo_box((1, 1, 164, 129), 164, 129), (0.5, 0.5, 1.0, 1.0))
        self.assertEqual(lsi.to_yolo_box((1, 1, 1, 1), 10, 10), (0.05, 0.05, 0.1, 0.1))

    def test_clips_partly_outside_box(self):
        self.assertEqual(lsi.to_yolo_box((-3, -5, 10, 10), 10, 10), (0.5, 0.5, 1.0, 1.0))

    def test_rejects_fully_outside_box(self):
        with self.assertRaises(ValueError):
            lsi.to_yolo_box((200, 10, 220, 20), 164, 129)

    def test_normalization_is_invariant_to_positive_affine_scale(self):
        raw = np.arange(1600, dtype=np.uint16).reshape(40, 40)
        np.testing.assert_array_equal(lsi.normalize_image(raw), lsi.normalize_image(raw * 2 + 10000))

    def test_flat_image_does_not_divide_by_zero(self):
        result = lsi.normalize_image(np.full((8, 10), 31000, dtype=np.uint16))
        self.assertEqual(result.dtype, np.uint8)
        self.assertFalse(result.any())

    def test_does_not_accept_8bit_image_as_raw(self):
        with self.assertRaises(ValueError):
            lsi.normalize_image(np.zeros((8, 10), dtype=np.uint8))

    def test_official_test_never_enters_training(self):
        self.assertEqual(lsi.select_split("Test", "02", {"02"}), "test")
        self.assertEqual(lsi.select_split("Train", "02", {"02"}), "val")
        self.assertEqual(lsi.select_split("Train", "01", {"02"}), "train")

    def test_full_conversion_preserves_negatives_and_audits_ambiguous_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "Detection"
            output = Path(temporary) / "converted"
            image_names = ["Train/01/00001.png", "Train/02/00002.png", "Test/01/00003.png",
                           "Test/01/00004.png", "Train/01/00005.png"]
            for name in image_names:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(np.arange(80, dtype=np.uint16).reshape(8, 10) + 31000).save(path)
            for subset in ("Train", "Test"):
                (source / subset / "annotations").mkdir()
            for name in (image_names[0], image_names[2]):
                subset, sequence, filename = name.split("/")
                target = source / subset / "annotations" / f"{sequence}_{Path(filename).stem}.txt"
                target.write_text(self.annotation(name), encoding="utf-8")
            (source / "Train" / "pos.lst").write_text(image_names[0] + "\n")
            (source / "Train" / "neg.lst").write_text(image_names[1] + "\n")
            (source / "Test" / "pos.lst").write_text("\n".join(image_names[2:4]))
            (source / "Test" / "neg.lst").write_text(image_names[3])
            report = lsi.prepare(source, output, {"02"})
            self.assertEqual([report["splits"][s]["images"] for s in ("train", "val", "test")], [1, 1, 1])
            self.assertEqual(report["splits"]["val"]["negative_images"], 1)
            self.assertEqual(len(report["audit"]["excluded_unlabeled_images"]), 2)
            label = (output / "labels" / "train" / "train_01_00001.txt").read_text()
            self.assertEqual(label, "0 0.35000000 0.50000000 0.50000000 0.75000000\n")
            config = json.loads((output / "dataset.yaml").read_text())
            self.assertEqual(config["nc"], 1)
            with self.assertRaises(ValueError):
                lsi.prepare(source, output, {"02"})

    def test_all_sequences_cannot_be_validation(self):
        with self.assertRaises(ValueError):
            lsi.prepare(Path("unused"), Path("unused"), {f"{i:02d}" for i in range(1, 7)})


if __name__ == "__main__":
    unittest.main()
