"""공식 LSI 검출 자료를 촬영 구간별로 분리한 YOLO 데이터로 변환합니다."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path, PurePosixPath

import numpy as np
from PIL import Image

ARCHIVE_MD5 = "39b3ff8745175789304c8d47c33af904"
SOURCE_URL = "https://e-archivo.uc3m.es/entities/publication/ca4cf1d7-506c-4155-bdfd-1b2ed1e0c1aa"
BOX_PATTERN = re.compile(
    r'Bounding box for object (\d+) "PASperson" \(Xmin, Ymin\) - '
    r'\(Xmax, Ymax\) : \((-?\d+), (-?\d+)\) - \((-?\d+), (-?\d+)\)'
)


def parse_annotation(text: str) -> tuple[str, list[tuple[int, int, int, int]]]:
    filename = re.search(r'^Image filename\s*:\s*"([^"]+)"', text, re.M)
    count = re.search(r'^Objects with ground truth\s*:\s*(\d+)', text, re.M)
    if filename is None or count is None:
        raise ValueError("LSI 주석의 파일명 또는 객체 수가 없습니다.")
    path = PurePosixPath(filename[1])
    if path.is_absolute() or ".." in path.parts or "\\" in filename[1]:
        raise ValueError("LSI 주석에 허용되지 않은 파일 경로가 있습니다.")
    matches = list(BOX_PATTERN.finditer(text))
    if len(matches) != int(count[1]):
        raise ValueError("주석의 객체 수와 사람 박스 수가 다릅니다.")
    if [int(m[1]) for m in matches] != list(range(1, len(matches) + 1)):
        raise ValueError("주석의 객체 번호가 중복되거나 누락되었습니다.")
    boxes = [tuple(int(m[i]) for i in range(2, 6)) for m in matches]
    return str(path), boxes


def to_yolo_box(box: tuple[int, int, int, int], width: int, height: int) -> tuple[float, ...]:
    if width <= 0 or height <= 0:
        raise ValueError("영상 크기는 양수여야 합니다.")
    xmin, ymin, xmax, ymax = box
    if xmax < xmin or ymax < ymin:
        raise ValueError("박스 좌표의 순서가 잘못되었습니다.")
    # PASCAL의 1부터 시작하는 포함 좌표를 픽셀 경계 좌표로 바꿉니다.
    x1, y1 = max(0, xmin - 1), max(0, ymin - 1)
    x2, y2 = min(width, xmax), min(height, ymax)
    if x2 <= x1 or y2 <= y1:
        raise ValueError("영상 안에 유효한 면적이 없는 박스입니다.")
    return ((x1 + x2) / (2 * width), (y1 + y2) / (2 * height),
            (x2 - x1) / width, (y2 - y1) / height)


def normalize_image(raw: np.ndarray) -> np.ndarray:
    if raw.ndim != 2 or raw.dtype != np.uint16:
        raise ValueError("LSI 입력은 16비트 단일 채널 영상이어야 합니다.")
    lower, upper = np.percentile(raw, [1, 99])
    if upper <= lower:
        return np.zeros(raw.shape, dtype=np.uint8)
    # LSI 값은 켈빈으로 해석하지 않습니다. 프레임별 상대 명암만 사용합니다.
    return np.clip((raw.astype(np.float32) - lower) * 255 / (upper - lower),
                   0, 255).astype(np.uint8)


def select_split(subset: str, sequence: str, validation: set[str]) -> str:
    if subset == "Test":
        return "test"
    if subset != "Train" or sequence not in {f"{i:02d}" for i in range(1, 7)}:
        raise ValueError("알 수 없는 LSI 촬영 구간입니다.")
    return "val" if sequence in validation else "train"


def extract_detection(archive: Path, destination: Path) -> Path:
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "md5").hexdigest()
    if digest != ARCHIVE_MD5:
        raise ValueError("공식 LSI 압축 파일의 MD5와 일치하지 않습니다. 다운로드를 확인하세요.")
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            path = PurePosixPath(member.name)
            if path.parts[:2] != ("LSIFIR", "Detection"):
                continue
            if member.isdir():
                continue
            if not member.isfile() or ".." in path.parts or path.is_absolute():
                raise ValueError("압축 파일에 허용되지 않은 항목이 있습니다.")
            if path.name.startswith("."):
                continue
            source.extract(member, destination, filter="data")
    return destination / "LSIFIR" / "Detection"


def prepare(source: Path, output: Path, validation: set[str]) -> dict:
    sequences = {f"{i:02d}" for i in range(1, 7)}
    if not validation or not validation < sequences:
        raise ValueError("검증 구간은 Train의 01~06 중 일부여야 합니다.")
    if output.exists():
        raise ValueError("출력 폴더가 이미 있습니다. 새 경로를 지정하세요.")
    annotations: dict[str, list[tuple[int, int, int, int]]] = {}
    negatives: set[str] = set()
    positives: set[str] = set()
    for subset in ("Train", "Test"):
        for annotation in sorted((source / subset / "annotations").glob("*.txt")):
            name, boxes = parse_annotation(annotation.read_text(encoding="latin-1"))
            if name in annotations or not name.startswith(subset + "/"):
                raise ValueError(f"중복되거나 잘못된 주석 경로: {name}")
            annotations[name] = boxes
        for label, target in (("pos", positives), ("neg", negatives)):
            target.update(line.strip() for line in
                          (source / subset / f"{label}.lst").read_text().splitlines()
                          if line.strip())
    images = sorted(source.glob("*/*/*.png"))
    names = {p.relative_to(source).as_posix() for p in images}
    if not images or not annotations.keys() <= names:
        raise ValueError("박스 주석에 대응하는 원본 영상이 없습니다.")
    if annotations.keys() & negatives:
        raise ValueError("박스 주석과 음성 목록이 충돌합니다.")
    # 공식 목록 일부의 경계 프레임이 어긋나 있으므로 누락 영상을 음성으로 추정하지 않습니다.
    confirmed_negatives = negatives - positives
    eligible = annotations.keys() | confirmed_negatives
    excluded = names - eligible
    report: dict = {
        "source": SOURCE_URL,
        "archive_md5": ARCHIVE_MD5,
        "validation_sequences": sorted(validation),
        "normalization": "uint16_percentile_1_99_to_uint8",
        "box_coordinates": "PASCAL_1_based_inclusive_clipped_to_image",
        "audit": {"excluded_unlabeled_images": sorted(excluded),
                  "missing_list_images": sorted((positives | negatives) - names),
                  "positive_list_without_annotation": sorted(positives - annotations.keys()),
                  "conflicting_list_images": sorted(positives & negatives)},
        "splits": {s: {"images": 0, "boxes": 0, "negative_images": 0,
                         "height_bins": {"under_10": 0, "10_19": 0, "20_39": 0, "40_plus": 0}}
                   for s in ("train", "val", "test")},
        "images": [],
    }
    for split in report["splits"]:
        (output / "images" / split).mkdir(parents=True)
        (output / "labels" / split).mkdir(parents=True)
    for image in images:
        name = image.relative_to(source).as_posix()
        if name not in eligible:
            continue
        subset, sequence, _ = PurePosixPath(name).parts
        split = select_split(subset, sequence, validation)
        with Image.open(image) as original:
            raw = np.array(original)
        height, width = raw.shape
        boxes = annotations.get(name, [])
        if name in negatives and boxes:
            raise ValueError(f"음성 목록 영상에 사람 주석이 있습니다: {name}")
        normalized = normalize_image(raw)
        stem = f"{subset.lower()}_{sequence}_{image.stem}"
        Image.fromarray(normalized).save(output / "images" / split / f"{stem}.png")
        labels = [to_yolo_box(box, width, height) for box in boxes]
        (output / "labels" / split / f"{stem}.txt").write_text(
            "".join("0 " + " ".join(f"{v:.8f}" for v in box) + "\n" for box in labels),
            encoding="utf-8")
        counts = report["splits"][split]
        counts["images"] += 1
        counts["boxes"] += len(labels)
        counts["negative_images"] += not labels
        for box in labels:
            h = round(box[3] * height, 6)
            key = "under_10" if h < 10 else "10_19" if h < 20 else "20_39" if h < 40 else "40_plus"
            counts["height_bins"][key] += 1
        report["images"].append({"id": stem, "source": name, "split": split,
                                 "width": width, "height": height})
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # JSON은 YAML의 부분집합이므로 경로 이스케이프도 구조적으로 처리합니다.
    config = {"path": output.resolve().as_posix(), "train": "images/train", "val": "images/val",
              "test": "images/test", "nc": 1, "names": ["person"]}
    (output / "dataset.yaml").write_text(json.dumps(config, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--archive", type=Path)
    group.add_argument("--source", type=Path, help="압축 해제된 Detection 폴더")
    parser.add_argument("--extract-to", type=Path, default=Path("data/raw/lsi-fir/extracted"))
    parser.add_argument("--output", type=Path, default=Path("data/private/lsi-fir"))
    parser.add_argument("--val-sequences", nargs="+", default=["02"])
    args = parser.parse_args()
    source = extract_detection(args.archive, args.extract_to) if args.archive else args.source
    report = prepare(source, args.output, set(args.val_sequences))
    print(json.dumps(report["splits"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
