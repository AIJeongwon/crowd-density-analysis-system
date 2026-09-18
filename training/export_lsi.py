"""학습 후보를 기존 라즈베리파이 검출기와 호환되는 ONNX로 변환합니다."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from yolov5_runtime import configure


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yolov5", type=Path, default=Path("models/yolov5-lsi"))
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("models/llvip-lsi-yolov5l-160.onnx"))
    parser.add_argument("--images", type=Path, default=Path("data/private/lsi-fir/images/val"))
    args = parser.parse_args()
    if args.output.exists():
        parser.error("기존 ONNX 파일을 덮어쓰지 않습니다. 새 경로를 지정하세요.")
    with configure(args.yolov5):
        import export
        import cv2
        import numpy as np
        import onnx
        import onnxruntime as ort
        import torch
        from models.experimental import attempt_load

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sensor-client"))
        from thermal_person_detector import YoloV5OnnxDetector, letterbox_image

        exported = export.run(weights=str(args.weights.resolve()), imgsz=[160, 160],
                              include=["onnx"], opset=13, device="cpu")
        paths = [Path(p) for p in exported if str(p).endswith(".onnx")]
        if len(paths) != 1:
            raise RuntimeError("ONNX 변환 결과를 확인할 수 없습니다.")
        model = onnx.load(str(paths[0]))
        onnx.checker.check_model(model)
        session = ort.InferenceSession(str(paths[0]), providers=["CPUExecutionProvider"])
        inputs = session.get_inputs()
        if len(inputs) != 1 or inputs[0].shape != [1, 3, 160, 160]:
            raise RuntimeError("ONNX 입력 형식이 기존 검출기와 다릅니다.")
        result = session.run(None, {inputs[0].name: np.zeros((1, 3, 160, 160), dtype=np.float32)})[0]
        if result.ndim != 3 or result.shape[2] != 6 or not np.isfinite(result).all():
            raise RuntimeError("ONNX 출력이 단일 사람 클래스 YOLOv5 형식이 아닙니다.")
        images = sorted(args.images.glob("*.png"))
        if not images:
            raise ValueError("ONNX 비교에 사용할 검증 영상이 없습니다.")
        reference = attempt_load(str(args.weights.resolve()), device=torch.device("cpu"))
        detector = YoloV5OnnxDetector(paths[0], 160, 0.25, 0.45, cv2, np, ort)
        checked = []
        for index in np.linspace(0, len(images) - 1, min(5, len(images)), dtype=int):
            image = cv2.imread(str(images[index]))
            padded, _ = letterbox_image(image, 160, cv2, np)
            blob = cv2.dnn.blobFromImage(padded, 1 / 255.0, (160, 160), swapRB=True)
            with torch.inference_mode():
                expected = reference(torch.from_numpy(blob))[0].numpy()
            actual = session.run(None, {inputs[0].name: blob})[0]
            np.testing.assert_allclose(actual, expected, rtol=1e-3, atol=1e-3)
            checked.append({"image": images[index].name, "detections": len(detector.detect(image)),
                            "maximum_absolute_error": float(np.max(np.abs(actual - expected)))})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(paths[0], args.output)
        with args.output.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        print(json.dumps({"file": str(args.output), "sha256": digest,
                          "bytes": args.output.stat().st_size, "output_shape": list(result.shape),
                          "comparison": checked}, indent=2))


if __name__ == "__main__":
    main()
