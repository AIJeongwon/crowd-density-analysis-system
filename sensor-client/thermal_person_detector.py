from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thermal_camera import (
    MINIMUM_AUTO_DISPLAY_SPAN,
    ThermalCapture,
    detect_y16_resolution,
    expand_temperature_range,
    is_window_visible,
    raw_to_temperature,
    rotate_frame,
    validate_device_path,
)


WINDOW_NAME = "CDAS Thermal Person Detector"


@dataclass(frozen=True)
class LetterboxTransform:
    scale: float
    padding_x: float
    padding_y: float


@dataclass(frozen=True)
class ModelCandidate:
    box: tuple[float, float, float, float]
    confidence: float


@dataclass(frozen=True)
class Detection:
    box: tuple[int, int, int, int]
    confidence: float


@dataclass(frozen=True)
class DetectionResult:
    detections: tuple[Detection, ...]
    inference_ms: float
    source_image: Any
    result_fps: float = 0.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LLVIP YOLOv5 ONNX 모델로 열화상에서 사람을 검출합니다."
    )
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--scale", type=int, default=8)
    parser.add_argument(
        "--backend",
        choices=["auto", "v4l2", "gstreamer"],
        default="auto",
    )
    parser.add_argument(
        "--rotate",
        type=int,
        choices=[0, 90, 180, 270],
        default=0,
    )
    parser.add_argument("--input-size", type=int, default=640)
    parser.add_argument("--inference-fps", type=float, default=4.0)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--nms-threshold", type=float, default=0.45)
    parser.add_argument(
        "--display-mode",
        choices=["synchronized", "live"],
        default="synchronized",
    )
    return parser


def validate_model_path(model_path: Path) -> Path:
    if model_path.suffix.lower() != ".onnx":
        raise ValueError("--model에는 ONNX 모델 파일을 지정해야 합니다.")
    if not model_path.is_file():
        raise ValueError(f"모델 파일을 찾을 수 없습니다: {model_path}")
    return model_path.resolve()


def validate_input_size(input_size: int) -> int:
    if not 160 <= input_size <= 1280:
        raise ValueError("--input-size는 160에서 1280 사이여야 합니다.")
    if input_size % 32 != 0:
        raise ValueError("--input-size는 32의 배수여야 합니다.")
    return input_size


def validate_inference_fps(inference_fps: float) -> float:
    if not 0.1 <= inference_fps <= 30.0:
        raise ValueError("--inference-fps는 0.1에서 30 사이여야 합니다.")
    return inference_fps


def validate_probability(value: float, option: str) -> float:
    if not 0.0 < value <= 1.0:
        raise ValueError(f"{option}는 0보다 크고 1 이하여야 합니다.")
    return value


def load_runtime_dependencies() -> tuple[Any, Any, Any]:
    try:
        import cv2
        import numpy as np
        import onnxruntime as ort
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "OpenCV, NumPy와 ONNX Runtime이 필요합니다. "
            "센서 클라이언트 문서의 LLVIP 실행 환경 준비 절차를 확인해 주세요."
        ) from exc
    return cv2, np, ort


def normalize_thermal_frame(
    temperature_frame: Any,
    minimum_span: float,
    np: Any,
) -> Any:
    lower = float(np.percentile(temperature_frame, 1.0))
    upper = float(np.percentile(temperature_frame, 99.0))
    if not np.isfinite(lower) or not np.isfinite(upper):
        raise RuntimeError("열화상 프레임의 명암 범위를 계산하지 못했습니다.")

    if upper <= lower:
        lower -= minimum_span / 2.0
        upper += minimum_span / 2.0
    else:
        lower, upper = expand_temperature_range(lower, upper, minimum_span)
    normalized = np.clip(
        (temperature_frame - lower) * (255.0 / (upper - lower)),
        0.0,
        255.0,
    ).astype(np.uint8)
    return np.repeat(normalized[:, :, np.newaxis], 3, axis=2)


def letterbox_image(
    image: Any,
    input_size: int,
    cv2: Any,
    np: Any,
) -> tuple[Any, LetterboxTransform]:
    height, width = image.shape[:2]
    scale = min(input_size / width, input_size / height)
    resized_width = int(round(width * scale))
    resized_height = int(round(height * scale))
    resized = cv2.resize(
        image,
        (resized_width, resized_height),
        interpolation=cv2.INTER_LINEAR,
    )

    horizontal_padding = (input_size - resized_width) / 2.0
    vertical_padding = (input_size - resized_height) / 2.0
    left = int(round(horizontal_padding - 0.1))
    top = int(round(vertical_padding - 0.1))

    canvas = np.full((input_size, input_size, 3), 114, dtype=np.uint8)
    canvas[top : top + resized_height, left : left + resized_width] = resized
    return canvas, LetterboxTransform(
        scale=scale,
        padding_x=horizontal_padding,
        padding_y=vertical_padding,
    )


def prediction_matrix(output: Any, np: Any) -> Any:
    if isinstance(output, (list, tuple)):
        if not output:
            raise RuntimeError("모델이 검출 결과를 반환하지 않았습니다.")
        output = output[0]

    matrix = np.asarray(output)
    if matrix.ndim >= 3 and matrix.shape[0] == 1:
        matrix = matrix[0]
    matrix = np.squeeze(matrix)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    if matrix.ndim != 2:
        raise RuntimeError(f"지원하지 않는 모델 출력 형태입니다: {matrix.shape}")
    if matrix.shape[1] < 6 and matrix.shape[0] >= 6:
        matrix = matrix.T
    if matrix.shape[1] < 6:
        raise RuntimeError(f"지원하지 않는 모델 출력 형태입니다: {matrix.shape}")
    return matrix


def extract_person_candidates(
    output: Any,
    confidence_threshold: float,
    np: Any,
) -> list[ModelCandidate]:
    candidates: list[ModelCandidate] = []
    for prediction in prediction_matrix(output, np):
        if not np.isfinite(prediction).all():
            continue

        class_scores = prediction[5:]
        class_id = int(np.argmax(class_scores))
        confidence = float(prediction[4] * class_scores[class_id])
        if class_id != 0 or confidence < confidence_threshold:
            continue

        center_x, center_y, width, height = map(float, prediction[:4])
        if width <= 0.0 or height <= 0.0:
            continue
        candidates.append(
            ModelCandidate(
                box=(
                    center_x - width / 2.0,
                    center_y - height / 2.0,
                    width,
                    height,
                ),
                confidence=confidence,
            )
        )
    return candidates


def flatten_nms_indices(indices: Any) -> list[int]:
    flattened: list[int] = []
    for index in indices:
        if isinstance(index, (list, tuple)):
            flattened.append(int(index[0]))
        elif hasattr(index, "item"):
            flattened.append(int(index.item()))
        else:
            flattened.append(int(index))
    return flattened


def restore_box(
    model_box: tuple[float, float, float, float],
    transform: LetterboxTransform,
    frame_size: tuple[int, int],
) -> tuple[int, int, int, int] | None:
    model_x, model_y, model_width, model_height = model_box
    frame_width, frame_height = frame_size
    x1 = (model_x - transform.padding_x) / transform.scale
    y1 = (model_y - transform.padding_y) / transform.scale
    x2 = (model_x + model_width - transform.padding_x) / transform.scale
    y2 = (model_y + model_height - transform.padding_y) / transform.scale

    x1 = min(max(x1, 0.0), float(frame_width - 1))
    y1 = min(max(y1, 0.0), float(frame_height - 1))
    x2 = min(max(x2, 0.0), float(frame_width))
    y2 = min(max(y2, 0.0), float(frame_height))
    if x2 <= x1 or y2 <= y1:
        return None
    return round(x1), round(y1), round(x2), round(y2)


class YoloV5OnnxDetector:
    def __init__(
        self,
        model_path: Path,
        input_size: int,
        confidence_threshold: float,
        nms_threshold: float,
        cv2: Any,
        np: Any,
        ort: Any,
    ) -> None:
        self.input_size = input_size
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.cv2 = cv2
        self.np = np
        try:
            options = ort.SessionOptions()
            options.graph_optimization_level = (
                ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            )
            self.session = ort.InferenceSession(
                str(model_path),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            model_input = self.session.get_inputs()[0]
            self.input_name = model_input.name
            model_height, model_width = model_input.shape[-2:]
            if (
                isinstance(model_height, int)
                and isinstance(model_width, int)
                and (model_height != input_size or model_width != input_size)
            ):
                raise ValueError(
                    "--input-size가 ONNX 모델의 입력 크기와 일치하지 않습니다. "
                    f"모델 입력: {model_width}x{model_height}"
                )
        except ValueError:
            raise
        except Exception as exc:
            raise RuntimeError(f"ONNX 모델을 열지 못했습니다: {exc}") from exc

    def detect(self, image: Any) -> list[Detection]:
        model_image, transform = letterbox_image(
            image,
            self.input_size,
            self.cv2,
            self.np,
        )
        blob = self.cv2.dnn.blobFromImage(
            model_image,
            scalefactor=1.0 / 255.0,
            size=(self.input_size, self.input_size),
            swapRB=True,
            crop=False,
        )
        try:
            output = self.session.run(None, {self.input_name: blob})
        except Exception as exc:
            raise RuntimeError(f"ONNX 추론에 실패했습니다: {exc}") from exc

        candidates = extract_person_candidates(
            output,
            self.confidence_threshold,
            self.np,
        )
        if not candidates:
            return []

        indices = self.cv2.dnn.NMSBoxes(
            [list(candidate.box) for candidate in candidates],
            [candidate.confidence for candidate in candidates],
            self.confidence_threshold,
            self.nms_threshold,
        )
        frame_height, frame_width = image.shape[:2]
        detections: list[Detection] = []
        for index in flatten_nms_indices(indices):
            candidate = candidates[index]
            box = restore_box(
                candidate.box,
                transform,
                (frame_width, frame_height),
            )
            if box is not None:
                detections.append(
                    Detection(box=box, confidence=candidate.confidence)
                )
        return detections


class AsyncDetector:
    def __init__(
        self,
        detector: YoloV5OnnxDetector,
        inference_fps: float,
    ) -> None:
        self.detector = detector
        self.inference_interval = 1.0 / inference_fps
        self.condition = threading.Condition()
        self.pending_image: Any | None = None
        self.latest_result: DetectionResult | None = None
        self.error: RuntimeError | None = None
        self.stopped = False
        self.next_inference_at = 0.0
        self.last_inference_at: float | None = None
        self.thread = threading.Thread(
            target=self._run,
            name="thermal-person-detector",
        )

    def start(self) -> None:
        self.thread.start()

    def submit(self, image: Any) -> None:
        with self.condition:
            if self.stopped or self.error is not None:
                return
            self.pending_image = image.copy()
            self.condition.notify()

    def get_latest(self) -> DetectionResult | None:
        with self.condition:
            if self.error is not None:
                raise self.error
            return self.latest_result

    def close(self) -> None:
        with self.condition:
            self.stopped = True
            self.pending_image = None
            self.condition.notify()
        if self.thread.is_alive():
            self.thread.join()

    def _run(self) -> None:
        while True:
            with self.condition:
                while True:
                    if self.stopped:
                        return
                    now = time.monotonic()
                    if (
                        self.pending_image is not None
                        and now >= self.next_inference_at
                    ):
                        break
                    wait_seconds = None
                    if self.pending_image is not None:
                        wait_seconds = max(0.0, self.next_inference_at - now)
                    self.condition.wait(timeout=wait_seconds)

                image = self.pending_image
                self.pending_image = None
                started_at = time.monotonic()
                self.next_inference_at = started_at + self.inference_interval
                previous_inference_at = self.last_inference_at
                self.last_inference_at = started_at

            try:
                detections = self.detector.detect(image)
            except Exception as exc:
                error = (
                    exc
                    if isinstance(exc, RuntimeError)
                    else RuntimeError(f"모델 추론 작업이 중단되었습니다: {exc}")
                )
                with self.condition:
                    self.error = error
                    self.condition.notify_all()
                return
            elapsed_ms = (time.monotonic() - started_at) * 1000.0
            result_fps = 0.0
            if previous_inference_at is not None:
                interval = started_at - previous_inference_at
                if interval > 0.0:
                    result_fps = 1.0 / interval

            with self.condition:
                self.latest_result = DetectionResult(
                    detections=tuple(detections),
                    inference_ms=elapsed_ms,
                    source_image=image,
                    result_fps=result_fps,
                )


def select_display_source(
    current_image: Any,
    result: DetectionResult | None,
    display_mode: str,
) -> Any:
    if display_mode == "synchronized" and result is not None:
        return result.source_image
    return current_image


def draw_detections(
    image: Any,
    result: DetectionResult | None,
    display_scale: int,
    display_fps: float,
    cv2: Any,
) -> None:
    detections = result.detections if result is not None else ()
    for detection in detections:
        x1, y1, x2, y2 = detection.box
        start = (x1 * display_scale, y1 * display_scale)
        end = (x2 * display_scale, y2 * display_scale)
        cv2.rectangle(image, start, end, (0, 255, 0), 2)

        label = f"PERSON {detection.confidence:.2f}"
        text_size, baseline = cv2.getTextSize(
            label,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            1,
        )
        text_y = start[1] - 7
        if text_y - text_size[1] < 0:
            text_y = min(start[1] + text_size[1] + 7, image.shape[0] - baseline)
        text_x = min(start[0], max(0, image.shape[1] - text_size[0] - 4))
        cv2.rectangle(
            image,
            (text_x, text_y - text_size[1] - 4),
            (text_x + text_size[0] + 4, text_y + baseline),
            (0, 0, 0),
            -1,
        )
        cv2.putText(
            image,
            label,
            (text_x + 2, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )

    inference_text = (
        "WARMING UP" if result is None else f"INFERENCE {result.inference_ms:.0f} MS"
    )
    status = f"PERSONS {len(detections)}   FPS {display_fps:.1f}   {inference_text}"
    status_scale = 0.52
    status_width = cv2.getTextSize(
        status,
        cv2.FONT_HERSHEY_SIMPLEX,
        status_scale,
        1,
    )[0][0]
    if status_width > image.shape[1] - 16:
        status_scale *= (image.shape[1] - 16) / status_width
    cv2.rectangle(image, (0, 0), (image.shape[1], 28), (0, 0, 0), -1)
    cv2.putText(
        image,
        status,
        (8, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        status_scale,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )


def run_viewer(
    capture: ThermalCapture,
    detector: YoloV5OnnxDetector,
    scale: int,
    rotate: int,
    display_mode: str,
    inference_fps: float,
    cv2: Any,
    np: Any,
) -> None:
    worker = AsyncDetector(detector, inference_fps)
    camera_fps = 0.0
    last_frame_at = time.monotonic()
    window_created = False

    capture.open()
    worker.start()
    try:
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)
        window_created = True
        while True:
            raw_frame = capture.read()
            temperature_frame = rotate_frame(
                raw_to_temperature(raw_frame, np),
                rotate,
                np,
            )
            model_image = normalize_thermal_frame(
                temperature_frame,
                MINIMUM_AUTO_DISPLAY_SPAN,
                np,
            )
            worker.submit(model_image)
            result = worker.get_latest()

            display_source = select_display_source(
                model_image,
                result,
                display_mode,
            )
            display = cv2.applyColorMap(
                display_source[:, :, 0],
                cv2.COLORMAP_INFERNO,
            )
            display = cv2.resize(
                display,
                (display.shape[1] * scale, display.shape[0] * scale),
                interpolation=cv2.INTER_CUBIC,
            )

            now = time.monotonic()
            elapsed = now - last_frame_at
            last_frame_at = now
            if elapsed > 0.0:
                current_fps = 1.0 / elapsed
                camera_fps = (
                    current_fps
                    if camera_fps == 0.0
                    else camera_fps * 0.9 + current_fps * 0.1
                )
            display_fps = camera_fps
            if (
                display_mode == "synchronized"
                and result is not None
                and result.result_fps > 0.0
            ):
                display_fps = result.result_fps
            draw_detections(display, result, scale, display_fps, cv2)
            cv2.imshow(WINDOW_NAME, display)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if not is_window_visible(cv2, WINDOW_NAME):
                break
    finally:
        worker.close()
        capture.close()
        if window_created:
            try:
                cv2.destroyWindow(WINDOW_NAME)
            except cv2.error:
                pass


def main() -> int:
    args = build_parser().parse_args()
    try:
        model_path = validate_model_path(args.model)
        device = validate_device_path(args.device)
        if (args.width is None) != (args.height is None):
            raise ValueError("--width와 --height는 함께 지정해야 합니다.")
        if args.width is None:
            width, height = detect_y16_resolution(device)
            print(f"Y16 해상도 감지: {width}x{height}")
        else:
            width, height = args.width, args.height
        if width <= 0 or height <= 0:
            raise ValueError("영상 크기는 1 이상이어야 합니다.")
        if not 1 <= args.scale <= 12:
            raise ValueError("--scale은 1에서 12 사이여야 합니다.")

        input_size = validate_input_size(args.input_size)
        inference_fps = validate_inference_fps(args.inference_fps)
        confidence = validate_probability(args.confidence, "--confidence")
        nms_threshold = validate_probability(
            args.nms_threshold,
            "--nms-threshold",
        )
        cv2, np, ort = load_runtime_dependencies()
        detector = YoloV5OnnxDetector(
            model_path=model_path,
            input_size=input_size,
            confidence_threshold=confidence,
            nms_threshold=nms_threshold,
            cv2=cv2,
            np=np,
            ort=ort,
        )
        capture = ThermalCapture(
            device=device,
            width=width,
            height=height,
            backend=args.backend,
            cv2=cv2,
            np=np,
        )
        run_viewer(
            capture=capture,
            detector=detector,
            scale=args.scale,
            rotate=args.rotate,
            display_mode=args.display_mode,
            inference_fps=inference_fps,
            cv2=cv2,
            np=np,
        )
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
