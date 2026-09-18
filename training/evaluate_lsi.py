"""지정한 입력 크기에서 공식 지표와 크기별 고정 임계값 재현율을 측정합니다."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from yolov5_runtime import configure


def match_at_threshold(detections, labels, confidence: float = 0.25) -> dict:
    import torch
    from torchvision.ops import box_iou

    selected = detections[detections[:, 4] >= confidence]
    selected = selected[np.argsort(-selected[:, 4], kind="stable")]
    counts = {key: {"total": 0, "detected": 0}
              for key in ("under_10", "10_19", "20_39", "40_plus")}
    heights = np.round(labels[:, 3] - labels[:, 1], 3) if len(labels) else []
    keys = ["under_10" if h < 10 else "10_19" if h < 20 else "20_39" if h < 40 else "40_plus"
            for h in heights]
    for key in keys:
        counts[key]["total"] += 1
    matched: set[int] = set()
    if len(selected) and len(labels):
        overlaps = box_iou(torch.as_tensor(selected[:, :4]), torch.as_tensor(labels)).numpy()
        for row in overlaps:
            available = [i for i in range(len(labels)) if i not in matched and row[i] >= 0.5]
            if available:
                index = max(available, key=lambda i: row[i])
                matched.add(index)
                counts[keys[index]]["detected"] += 1
    return {"true_positives": len(matched), "false_positives": len(selected) - len(matched),
            "false_negatives": len(labels) - len(matched), "height_bins": counts}


def load_labeled_predictions(root: Path, predictions: dict, split: str) -> list:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    frames = []
    for image in manifest["images"]:
        if image["split"] != split:
            continue
        label_file = root / "labels" / split / f"{image['id']}.txt"
        rows = [[float(v) for v in line.split()[1:]]
                for line in label_file.read_text().splitlines() if line.strip()]
        boxes = np.array(rows, dtype=np.float32).reshape(-1, 4)
        centers, sizes = boxes[:, :2].copy(), boxes[:, 2:].copy()
        boxes[:, :2], boxes[:, 2:] = centers - sizes / 2, centers + sizes / 2
        boxes *= [image["width"], image["height"], image["width"], image["height"]]
        detections = np.array(predictions.get(image["id"], []), dtype=np.float32).reshape(-1, 6)
        frames.append((detections, boxes))
    return frames


def summarize_predictions(frames: list, confidence: float) -> dict:
    totals = {"true_positives": 0, "false_positives": 0, "false_negatives": 0,
              "negative_frames": 0, "negative_frames_with_detections": 0,
              "frame_count": 0, "count_absolute_error_sum": 0, "count_signed_error_sum": 0,
              "height_bins": {k: {"total": 0, "detected": 0}
                              for k in ("under_10", "10_19", "20_39", "40_plus")}}
    for detections, boxes in frames:
        count = match_at_threshold(detections, boxes, confidence)
        # 박스 위치가 틀려도 인원수는 같을 수 있으므로 인원수 오차를 별도로 셉니다.
        count_error = count["false_positives"] - count["false_negatives"]
        totals["frame_count"] += 1
        totals["count_absolute_error_sum"] += abs(count_error)
        totals["count_signed_error_sum"] += count_error
        if not len(boxes):
            totals["negative_frames"] += 1
            totals["negative_frames_with_detections"] += int(count["false_positives"] > 0)
        for key in ("true_positives", "false_positives", "false_negatives"):
            totals[key] += count[key]
        for key in totals["height_bins"]:
            for metric in ("total", "detected"):
                totals["height_bins"][key][metric] += count["height_bins"][key][metric]
    for counts in totals["height_bins"].values():
        counts["recall"] = counts["detected"] / counts["total"] if counts["total"] else None
    tp, fp, fn = (totals[k] for k in ("true_positives", "false_positives", "false_negatives"))
    totals["precision"] = tp / (tp + fp) if tp + fp else None
    totals["recall"] = tp / (tp + fn) if tp + fn else None
    totals["negative_frame_false_positive_rate"] = (
        totals["negative_frames_with_detections"] / totals["negative_frames"]
        if totals["negative_frames"] else None)
    totals["count_mae"] = (totals["count_absolute_error_sum"] / totals["frame_count"]
                           if totals["frame_count"] else None)
    totals["count_mean_signed_error"] = (totals["count_signed_error_sum"] / totals["frame_count"]
                                         if totals["frame_count"] else None)
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yolov5", type=Path, default=Path("models/yolov5-lsi"))
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=Path("data/private/lsi-fir/dataset.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=("train", "val", "test"), default="val")
    parser.add_argument("--device", default="0")
    parser.add_argument("--input-size", type=int, default=160)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--rect", action="store_true", help="종횡비를 유지하고 패딩을 32배수로 줄입니다.")
    args = parser.parse_args()
    if args.input_size < 32 or args.input_size % 32 or args.batch_size < 1:
        parser.error("입력 크기는 32의 양의 배수, 배치 크기는 1 이상이어야 합니다.")
    if args.output.exists():
        parser.error("평가 결과 경로가 이미 있습니다.")
    args.output.mkdir(parents=True)
    with configure(args.yolov5):
        import torch
        import val
        from models.experimental import attempt_load
        from utils.callbacks import Callbacks
        from utils.dataloaders import create_dataloader
        from utils.general import check_dataset

        device = torch.device("cpu" if args.device == "cpu" else f"cuda:{args.device}")
        model = attempt_load(str(args.weights.resolve()), device=device)
        data = check_dataset(str(args.data.resolve()), autodownload=False)
        loader, dataset = create_dataloader(data[args.split], args.input_size, args.batch_size, 32, False,
                                            rect=args.rect, pad=0.0, workers=0)
        shapes = np.unique(dataset.batch_shapes, axis=0).tolist() if args.rect else [
            [args.input_size, args.input_size]]
        if len(shapes) != 1:
            raise ValueError("고정 입력 비교에는 모든 영상의 패딩 후 크기가 같아야 합니다.")
        predictions = {}

        def collect(pred, predn, path, names, image):
            predictions[path.stem] = predn.detach().cpu().numpy().tolist()

        callbacks = Callbacks()
        callbacks.register_action("on_val_image_end", callback=collect)
        metrics, _, times = val.run(data=data, model=model, dataloader=loader, imgsz=args.input_size,
                                    batch_size=args.batch_size, conf_thres=0.001, iou_thres=0.45,
                                    half=False, plots=False, callbacks=callbacks,
                                    save_dir=args.output)
        frames = load_labeled_predictions(args.data.parent, predictions, args.split)
        totals = summarize_predictions(frames, 0.25)
        with args.weights.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        report = {"weights": args.weights.as_posix(), "weights_sha256": digest,
                  "split": args.split, "images": len(frames), "input_shape": [1, 3, *shapes[0]],
                  "rect": args.rect, "evaluation_batch_size": args.batch_size, "precision_mode": "float32",
                  "nms_iou": 0.45, "count_confidence": 0.25, "match_iou": 0.5,
                  "ap50": float(metrics[2]), "ap50_95": float(metrics[3]),
                  "counts_at_025": totals, "gpu_batch_timings_ms_per_image": list(times)}
        (args.output / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (args.output / "predictions.json").write_text(json.dumps(predictions), encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
