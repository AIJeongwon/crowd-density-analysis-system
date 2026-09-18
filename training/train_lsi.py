"""기존 LLVIP 가중치를 유지한 채 LSI 학습 후보를 별도 생성합니다."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from yolov5_runtime import configure

BASE_SHA256 = "79159b97fb7663bd86562d0b2cc0e377762bd0031a90dcdf1f061e45e0d860aa"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yolov5", type=Path, default=Path("models/yolov5-lsi"))
    parser.add_argument("--weights", type=Path, default=Path("models/yolov5_trained_model/yolov5_infrared.pt"))
    parser.add_argument("--data", type=Path, default=Path("data/private/lsi-fir/dataset.yaml"))
    parser.add_argument("--output", type=Path, default=Path("outputs/lsi-training/candidate"))
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--device", default="0")
    parser.add_argument("--input-size", type=int, default=160)
    parser.add_argument("--hyp", type=Path, default=Path(__file__).with_name("hyp-lsi.yaml"))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.epochs < 2 or args.batch_size < 1:
        parser.error("학습 횟수는 2 이상, 배치 크기는 1 이상이어야 합니다.")
    if args.input_size < 32 or args.input_size % 32:
        parser.error("입력 크기는 32의 양의 배수여야 합니다.")
    if args.output.exists():
        parser.error("학습 결과 폴더가 이미 있습니다. 새 경로를 지정하세요.")
    with args.weights.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != BASE_SHA256:
            parser.error("검증한 LLVIP 원본 가중치와 해시가 다릅니다.")
    with configure(args.yolov5):
        import train
        train.run(
            weights=str(args.weights.resolve()), data=str(args.data.resolve()),
            hyp=str(args.hyp.resolve()),
            imgsz=args.input_size, epochs=args.epochs, batch_size=args.batch_size,
            device=args.device, workers=0, cache="ram", seed=args.seed,
            project=str(args.output.resolve().parent), name=args.output.name,
            patience=5, noplots=True, exist_ok=False,
        )


if __name__ == "__main__":
    main()
