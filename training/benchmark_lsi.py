"""동일 가중치의 정적 ONNX 입력별 CPU 단일 프레임 추론 시간을 비교합니다."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import time
from pathlib import Path

from yolov5_runtime import configure


def parse_shape(value: str) -> tuple[int, int]:
    try:
        height, width = (int(part) for part in value.lower().split("x"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("입력 크기는 높이x너비 형식이어야 합니다.") from exc
    if min(height, width) < 32 or height % 32 or width % 32:
        raise argparse.ArgumentTypeError("높이와 너비는 각각 32의 양의 배수여야 합니다.")
    return height, width


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yolov5", type=Path, default=Path("models/yolov5-lsi"))
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--images", type=Path, default=Path("data/private/lsi-fir/images/val"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shapes", type=parse_shape, nargs="+",
                        default=[(128, 160), (160, 160), (256, 256), (320, 320)])
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if min(args.threads, args.warmup, args.iterations, args.rounds) < 1:
        parser.error("스레드 수와 반복 횟수는 모두 1 이상이어야 합니다.")
    if len(set(args.shapes)) != len(args.shapes):
        parser.error("입력 크기가 중복되었습니다.")
    if args.output.exists():
        parser.error("기존 벤치마크 결과를 덮어쓰지 않습니다.")
    images = sorted(args.images.glob("*.png"))
    if not images:
        parser.error("검증 영상이 없습니다.")
    args.output.mkdir(parents=True)
    with configure(args.yolov5):
        import cv2
        import numpy as np
        import onnxruntime as ort
        import torch
        from export import export_onnx
        from models.experimental import attempt_load
        from models.yolo import Detect
        from utils.augmentations import letterbox

        torch.set_num_threads(args.threads)
        cv2.setNumThreads(1)
        model = attempt_load(str(args.weights.resolve()), device=torch.device("cpu"))
        heads = [module for module in model.modules() if isinstance(module, Detect)]
        for head in heads:
            head.inplace = False
            head.dynamic = False
        sample_paths = [images[index] for index in np.linspace(0, len(images) - 1,
                                                             min(16, len(images)), dtype=int)]
        samples = [cv2.imread(str(path)) for path in sample_paths]
        if any(sample is None for sample in samples):
            raise ValueError("검증 영상을 읽을 수 없습니다.")
        blobs = {}
        results = {}
        with torch.inference_mode():
            for height, width in args.shapes:
                name = f"{height}x{width}"
                blobs[name] = [cv2.dnn.blobFromImage(
                    letterbox(sample, (height, width), auto=False)[0],
                    1 / 255.0, (width, height), swapRB=True) for sample in samples]
                for head in heads:
                    head.export = True
                dummy = torch.zeros(1, 3, height, width)
                for _ in range(2):
                    model(dummy)
                output, _ = export_onnx(model, dummy, args.output / f"{name}.pt",
                                        opset=13, dynamic=False, simplify=False)
                if output is None:
                    raise RuntimeError(f"{name} ONNX 변환에 실패했습니다.")
                options = ort.SessionOptions()
                options.intra_op_num_threads = args.threads
                options.inter_op_num_threads = 1
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                session = ort.InferenceSession(str(output), sess_options=options,
                                               providers=["CPUExecutionProvider"])
                input_info = session.get_inputs()[0]
                if input_info.shape != [1, 3, height, width]:
                    raise ValueError("변환된 입력 크기가 요청한 크기와 다릅니다.")
                for head in heads:
                    head.export = False
                errors = []
                for blob in blobs[name][:5]:
                    expected = model(torch.from_numpy(blob))[0].numpy()
                    actual = session.run(None, {input_info.name: blob})[0]
                    if not np.isfinite(actual).all():
                        raise ValueError("추론 결과에 유효하지 않은 값이 있습니다.")
                    np.testing.assert_allclose(actual, expected, rtol=1e-3, atol=1e-3)
                    errors.append(float(np.max(np.abs(actual - expected))))
                results[name] = {"input_shape": [1, 3, height, width],
                                 "input_area_relative_to_160": height * width / (160 * 160),
                                 "onnx_bytes": Path(output).stat().st_size,
                                 "parity_max_absolute_errors": errors, "rounds_ms": []}
                del session

        # 실행 순서를 바꾸고 매 회 세션 예열 후 측정하여 첫 실행 비용을 제외합니다.
        generator = random.Random(42)
        orders = []
        for _ in range(args.rounds):
            order = list(results)
            generator.shuffle(order)
            orders.append(order)
            for name in order:
                options = ort.SessionOptions()
                options.intra_op_num_threads = args.threads
                options.inter_op_num_threads = 1
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                session = ort.InferenceSession(str(args.output / f"{name}.onnx"),
                                               sess_options=options, providers=["CPUExecutionProvider"])
                input_name = session.get_inputs()[0].name
                for index in range(args.warmup):
                    session.run(None, {input_name: blobs[name][index % len(samples)]})
                elapsed = []
                for index in range(args.iterations):
                    feed = {input_name: blobs[name][index % len(samples)]}
                    start = time.perf_counter_ns()
                    session.run(None, feed)
                    elapsed.append((time.perf_counter_ns() - start) / 1_000_000)
                results[name]["rounds_ms"].append(elapsed)
                del session
        for result in results.values():
            durations = np.array(result["rounds_ms"]).ravel()
            result["median_ms"] = float(np.median(durations))
            result["p95_ms"] = float(np.percentile(durations, 95))
            result["round_medians_ms"] = [float(np.median(row)) for row in result["rounds_ms"]]
        with args.weights.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        report = {"weights_sha256": digest, "platform": platform.platform(),
                  "processor": platform.processor(), "onnxruntime_version": ort.__version__,
                  "provider": "CPUExecutionProvider", "precision": "float32", "batch_size": 1,
                  "intra_op_threads": args.threads, "inter_op_threads": 1,
                  "warmup_per_round": args.warmup, "iterations_per_round": args.iterations,
                  "scope": "onnx_forward_only_excludes_preprocessing_nms_capture_display",
                  "round_orders": orders, "sample_images": [path.name for path in sample_paths],
                  "results": results}
        (args.output / "benchmark.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({name: {key: row[key] for key in ("median_ms", "p95_ms", "round_medians_ms")}
                          for name, row in results.items()}, indent=2))


if __name__ == "__main__":
    main()
