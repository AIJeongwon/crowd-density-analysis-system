"""학습 구간의 작은 사람과 오검출 배경을 보강하고 저대비 변형을 추가합니다."""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
from pathlib import Path

import numpy as np
from PIL import Image


def reduce_contrast(image: np.ndarray, factor: float) -> np.ndarray:
    if image.ndim != 2 or image.dtype != np.uint8:
        raise ValueError("대비 변형에는 8비트 단일 채널 영상이 필요합니다.")
    if not 0 < factor <= 1:
        raise ValueError("대비 배율은 0 초과 1 이하여야 합니다.")
    # 센서의 섭씨값을 추정하지 않고 중간 명도 주변의 대비만 줄입니다.
    return np.clip(np.rint(127.5 + factor * (image.astype(np.float32) - 127.5)),
                   0, 255).astype(np.uint8)


def validate_mining(metadata: dict, manifest: dict, predictions: dict) -> None:
    identifiers = {row["id"] for row in manifest["images"] if row["split"] == "train"}
    if metadata["split"] != "train" or metadata["input_shape"] != [1, 3, 160, 160]:
        raise ValueError("160 입력의 학습 구간 평가만 오검출 선정에 사용할 수 있습니다.")
    if metadata["images"] != len(identifiers) or not set(predictions) <= identifiers:
        raise ValueError("오검출 평가에 다른 구간의 영상이 포함되었거나 자료 수가 다릅니다.")


def variants_for(split: str, heights: list[float], predictions: list,
                 index: int) -> list[tuple[str, float]]:
    if split != "train":
        return []
    small = any(10 <= height < 20 for height in heights)
    hard_negative = not heights and any(row[4] >= 0.25 for row in predictions)
    variants = []
    if small or hard_negative:
        variants.extend([("repeat1", 1.0), ("repeat2", 1.0)])
    if index % 4 == 0 or small or hard_negative:
        variants.append(("contrast050", 0.5))
    return variants


def prepare(source: Path, mining: Path, output: Path) -> dict:
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    metadata = json.loads((mining / "metrics.json").read_text(encoding="utf-8"))
    predictions = json.loads((mining / "predictions.json").read_text(encoding="utf-8"))
    validate_mining(metadata, manifest, predictions)
    if output.exists():
        raise ValueError("기존 자료를 덮어쓰지 않습니다. 새 출력 경로를 지정하세요.")
    records = manifest["images"]
    if len({row["id"] for row in records}) != len(records):
        raise ValueError("원본 자료에 중복된 식별자가 있습니다.")
    for row in records:
        if row["split"] not in {"train", "val", "test"} or not re.fullmatch(
                r"(?:train|test)_\d{2}_\d+", row["id"]):
            raise ValueError("원본 자료에 허용되지 않은 식별자나 구간이 있습니다.")
    for split in ("train", "val", "test"):
        (output / "images" / split).mkdir(parents=True)
        (output / "labels" / split).mkdir(parents=True)
    report = copy.deepcopy(manifest)
    report["images"] = []
    report["refinement"] = {"source": source.resolve().as_posix(),
                            "mining_weights_sha256": metadata["weights_sha256"],
                            "mining_split": "train", "hard_negative_confidence": 0.25,
                            "small_source_images": [], "hard_negative_images": [],
                            "added_training_images": 0, "contrast_scale": 0.5}
    report["splits"] = {split: {"images": 0, "boxes": 0, "negative_images": 0,
                                "height_bins": {key: 0 for key in
                                                ("under_10", "10_19", "20_39", "40_plus")}}
                        for split in ("train", "val", "test")}
    train_index = 0
    for row in records:
        split, identifier = row["split"], row["id"]
        image_path = source / "images" / split / f"{identifier}.png"
        label_path = source / "labels" / split / f"{identifier}.txt"
        labels = [line.split() for line in label_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if any(len(label) != 5 or label[0] != "0" for label in labels):
            raise ValueError("사람 클래스의 YOLO 박스 주석이 아닙니다.")
        heights = [round(float(label[4]) * row["height"], 3) for label in labels]
        predictions_for_image = predictions.get(identifier, [])
        variants = variants_for(split, heights, predictions_for_image, train_index)
        if split == "train":
            train_index += 1
            if any(10 <= height < 20 for height in heights):
                report["refinement"]["small_source_images"].append(identifier)
            if not heights and any(pred[4] >= 0.25 for pred in predictions_for_image):
                report["refinement"]["hard_negative_images"].append(identifier)
        for suffix, factor in [("", 1.0), *variants]:
            target_id = f"{identifier}__{suffix}" if suffix else identifier
            target_image = output / "images" / split / f"{target_id}.png"
            if factor == 1:
                shutil.copy2(image_path, target_image)
            else:
                with Image.open(image_path) as image:
                    transformed = reduce_contrast(np.array(image), factor)
                Image.fromarray(transformed).save(target_image)
            shutil.copy2(label_path, output / "labels" / split / f"{target_id}.txt")
            derived = {**row, "id": target_id, "variant": suffix or "original",
                       "source_id": identifier}
            report["images"].append(derived)
            counts = report["splits"][split]
            counts["images"] += 1
            counts["boxes"] += len(labels)
            counts["negative_images"] += not labels
            for height in heights:
                key = "under_10" if height < 10 else "10_19" if height < 20 else "20_39" if height < 40 else "40_plus"
                counts["height_bins"][key] += 1
            report["refinement"]["added_training_images"] += bool(suffix)
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    config = {"path": output.resolve().as_posix(), "train": "images/train", "val": "images/val",
              "test": "images/test", "nc": 1, "names": ["person"]}
    (output / "dataset.yaml").write_text(json.dumps(config, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/private/lsi-fir"))
    parser.add_argument("--mining", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = prepare(args.source, args.mining, args.output)
    print(json.dumps({"splits": report["splits"], "refinement": report["refinement"]}, indent=2))


if __name__ == "__main__":
    main()
