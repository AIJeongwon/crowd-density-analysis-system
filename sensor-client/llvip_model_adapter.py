"""LLVIP YOLOv5 ONNX adapter for the threaded sensor client.

The thermal sensor worker already rotates Lepton frames clockwise by 90 degrees.
This adapter preserves that orientation and only converts the radiometric Y16
pixels into the three-channel grayscale input expected by the LLVIP model.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CENTIKELVIN_OFFSET = 27315.0
MINIMUM_DISPLAY_SPAN_CELSIUS = 2.0
DEFAULT_INPUT_SIZE = 160
CONFIDENCE_THRESHOLD = 0.25
NMS_THRESHOLD = 0.45


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


def load_runtime_dependencies() -> tuple[Any, Any, Any]:
    try:
        import cv2
        import numpy as np
        import onnxruntime as ort
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "OpenCV, NumPy, and ONNX Runtime are required; "
            "prepare the Raspberry Pi virtual environment described in "
            "sensor-client/README.md"
        ) from exc
    return cv2, np, ort


def validate_model_path(model_path: Path) -> Path:
    path = Path(model_path)
    if path.suffix.lower() != ".onnx":
        raise ValueError("LLVIP model_path must point to an ONNX model")
    if not path.is_file():
        raise ValueError(f"LLVIP ONNX model was not found: {path}")
    return path.resolve()


def resolve_input_size(input_shape: Any) -> int:
    """Return a supported square model size, defaulting for dynamic shapes."""

    try:
        model_height, model_width = input_shape[-2:]
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"unsupported ONNX input shape: {input_shape}") from exc

    if isinstance(model_height, int) and isinstance(model_width, int):
        if model_height != model_width:
            raise RuntimeError(
                "LLVIP ONNX input must be square: "
                f"received {model_width}x{model_height}"
            )
        input_size = model_width
    else:
        input_size = DEFAULT_INPUT_SIZE

    if input_size < 160 or input_size > 1280 or input_size % 32 != 0:
        raise RuntimeError(
            "LLVIP ONNX input size must be a multiple of 32 between 160 and 1280"
        )
    return input_size


def thermal_frame_to_image(sensor_data: dict[str, Any], np: Any) -> Any:
    thermal = sensor_data.get("thermal")
    if not isinstance(thermal, dict):
        raise RuntimeError("model input thermal must be a dictionary")

    width = thermal.get("width")
    height = thermal.get("height")
    pixels = thermal.get("pixels")
    if (
        isinstance(width, bool)
        or not isinstance(width, int)
        or width <= 0
        or isinstance(height, bool)
        or not isinstance(height, int)
        or height <= 0
    ):
        raise RuntimeError("thermal width and height must be positive integers")

    try:
        raw_frame = np.asarray(pixels, dtype=np.uint16)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("thermal pixels must contain Y16 integer values") from exc
    if raw_frame.size != width * height:
        raise RuntimeError(
            f"thermal pixels must contain exactly {width * height} values"
        )

    # ThermalSensorWorker already produced a clockwise-rotated row-major frame.
    # Reshaping with its published dimensions keeps that orientation unchanged.
    raw_frame = raw_frame.reshape((height, width))
    temperature_frame = (
        raw_frame.astype(np.float32) - CENTIKELVIN_OFFSET
    ) / 100.0
    return normalize_thermal_frame(
        temperature_frame,
        minimum_span=MINIMUM_DISPLAY_SPAN_CELSIUS,
        np=np,
    )


def normalize_thermal_frame(
    temperature_frame: Any,
    *,
    minimum_span: float,
    np: Any,
) -> Any:
    lower = float(np.percentile(temperature_frame, 1.0))
    upper = float(np.percentile(temperature_frame, 99.0))
    if not np.isfinite(lower) or not np.isfinite(upper):
        raise RuntimeError("failed to calculate the thermal intensity range")

    if upper <= lower:
        lower -= minimum_span / 2.0
        upper += minimum_span / 2.0
    elif upper - lower < minimum_span:
        center = (lower + upper) / 2.0
        lower = center - minimum_span / 2.0
        upper = center + minimum_span / 2.0

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
            raise RuntimeError("ONNX model returned no detection output")
        output = output[0]

    matrix = np.asarray(output)
    if matrix.ndim >= 3 and matrix.shape[0] == 1:
        matrix = matrix[0]
    matrix = np.squeeze(matrix)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    if matrix.ndim != 2:
        raise RuntimeError(f"unsupported ONNX output shape: {matrix.shape}")
    if matrix.shape[1] < 6 and matrix.shape[0] >= 6:
        matrix = matrix.T
    if matrix.shape[1] < 6:
        raise RuntimeError(f"unsupported ONNX output shape: {matrix.shape}")
    return matrix


def extract_person_candidates(
    output: Any,
    *,
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
    if indices is None:
        return []
    flattened: list[int] = []
    for index in indices:
        if isinstance(index, (list, tuple)):
            if index:
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


def debug_output_path(sensor_data: dict[str, Any]) -> Path | None:
    debug = sensor_data.get("debug")
    if not isinstance(debug, dict) or debug.get("enabled") is not True:
        return None

    output_dir = debug.get("output_dir")
    if not isinstance(output_dir, str) or not output_dir.strip():
        raise RuntimeError("debug output_dir must be a non-empty path")

    fused_at = sensor_data.get("fused_at")
    if not isinstance(fused_at, str):
        raise RuntimeError("debug inference image requires fused_at")
    normalized_timestamp = (
        fused_at[:-1] + "+00:00" if fused_at.endswith("Z") else fused_at
    )
    try:
        timestamp = datetime.fromisoformat(normalized_timestamp)
    except ValueError as exc:
        raise RuntimeError("fused_at must be an ISO 8601 timestamp") from exc
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    timestamp = timestamp.astimezone(timezone.utc)
    filename = timestamp.strftime("%Y%m%dT%H%M%S_%fZ_inference.png")
    return Path(output_dir).expanduser() / filename


def save_debug_detection_image(
    sensor_data: dict[str, Any],
    image: Any,
    detections: tuple[Detection, ...],
    cv2: Any,
) -> Path | None:
    destination = debug_output_path(sensor_data)
    if destination is None:
        return None

    temporary_path: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        annotated = cv2.applyColorMap(image[:, :, 0], cv2.COLORMAP_INFERNO)
        for detection in detections:
            x1, y1, x2, y2 = detection.box
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 1)
            label = f"PERSON {detection.confidence:.2f}"
            cv2.putText(
                annotated,
                label,
                (x1, max(y1 - 3, 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.3,
                (0, 255, 0),
                1,
                cv2.LINE_AA,
            )

        encoded_ok, encoded = cv2.imencode(".png", annotated)
        if not encoded_ok:
            raise RuntimeError("OpenCV failed to encode debug inference image")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(encoded.tobytes())
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, destination)
        return destination
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"failed to save debug inference image: {exc}"
        ) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


class ModelAdapter:
    """Model adapter loaded by ModelAdapterWorker."""

    def __init__(self, model_path: Path) -> None:
        self.model_path = validate_model_path(model_path)
        self.cv2, self.np, self.ort = load_runtime_dependencies()
        try:
            options = self.ort.SessionOptions()
            options.graph_optimization_level = (
                self.ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            )
            self.session = self.ort.InferenceSession(
                str(self.model_path),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            model_input = self.session.get_inputs()[0]
            self.input_name = model_input.name
            self.input_size = resolve_input_size(model_input.shape)
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"failed to open LLVIP ONNX model: {exc}") from exc

    def infer(self, sensor_data: dict[str, Any]) -> dict[str, Any]:
        image = thermal_frame_to_image(sensor_data, self.np)
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
            raise RuntimeError(f"LLVIP ONNX inference failed: {exc}") from exc

        candidates = extract_person_candidates(
            output,
            confidence_threshold=CONFIDENCE_THRESHOLD,
            np=self.np,
        )
        selected_candidates: list[ModelCandidate] = []
        if candidates:
            indices = flatten_nms_indices(
                self.cv2.dnn.NMSBoxes(
                    [list(candidate.box) for candidate in candidates],
                    [candidate.confidence for candidate in candidates],
                    CONFIDENCE_THRESHOLD,
                    NMS_THRESHOLD,
                )
            )
            selected_candidates = [
                candidates[index]
                for index in indices
                if 0 <= index < len(candidates)
            ]

        frame_height, frame_width = image.shape[:2]
        detections: list[Detection] = []
        for candidate in selected_candidates:
            box = restore_box(
                candidate.box,
                transform,
                (frame_width, frame_height),
            )
            if box is not None:
                detections.append(
                    Detection(box=box, confidence=candidate.confidence)
                )
        finalized_detections = tuple(detections)
        save_debug_detection_image(
            sensor_data,
            image,
            finalized_detections,
            self.cv2,
        )

        if not finalized_detections:
            return {"people_count": 0, "confidence": 0.0}
        selected_confidences = [
            detection.confidence for detection in finalized_detections
        ]
        return {
            "people_count": len(finalized_detections),
            "confidence": float(
                sum(selected_confidences) / len(selected_confidences)
            ),
        }
