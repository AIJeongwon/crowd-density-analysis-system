"""실제 학습 로더의 증강 영상과 변환된 정답 박스를 표본 검사합니다."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import yaml

from yolov5_runtime import configure


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yolov5", type=Path, default=Path("models/yolov5-lsi"))
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--hyp", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=512)
    args = parser.parse_args()
    if args.samples < 1 or args.output.exists():
        parser.error("표본 수는 양수여야 하며 출력 경로는 새 폴더여야 합니다.")
    args.output.mkdir(parents=True)
    with configure(args.yolov5):
        import torch
        from utils.dataloaders import create_dataloader
        from utils.general import check_dataset

        data = check_dataset(str(args.data.resolve()), autodownload=False)
        hyp = yaml.safe_load(args.hyp.read_text(encoding="utf-8"))
        _, dataset = create_dataloader(data["train"], 160, 32, 32, False, hyp=hyp,
                                        augment=True, workers=0)
        random.seed(42)
        np.random.seed(42)
        torch.manual_seed(42)
        bins = {key: 0 for key in ("under_10", "10_19", "20_39", "40_plus")}
        sheet = Image.new("RGB", (800, 880), (24, 24, 24))
        draw = ImageDraw.Draw(sheet)
        selected = random.sample(range(len(dataset)), min(args.samples, len(dataset)))
        shown = 0
        for index in selected:
            tensor, labels, path, _ = dataset[index]
            for height in labels[:, 5].numpy() * 160:
                key = "under_10" if height < 10 else "10_19" if height < 20 else "20_39" if height < 40 else "40_plus"
                bins[key] += 1
            if shown < 16 and len(labels):
                image = Image.fromarray(tensor.permute(1, 2, 0).numpy()).resize(
                    (200, 200), Image.Resampling.NEAREST)
                pen = ImageDraw.Draw(image)
                for label in labels.numpy():
                    x, y, width, height = label[2:] * 200
                    pen.rectangle((x - width / 2, y - height / 2, x + width / 2, y + height / 2),
                                  outline=(0, 255, 100), width=1)
                x, y = (shown % 4) * 200, (shown // 4) * 220
                sheet.paste(image, (x, y))
                draw.text((x + 4, y + 203), Path(path).stem[:25], fill="white")
                shown += 1
        sheet.save(args.output / "samples.png")
        report = {"frames": len(selected), "height_bins_in_160_input": bins,
                  "data": args.data.as_posix(), "hyp": hyp, "seed": 42}
        (args.output / "samples.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
