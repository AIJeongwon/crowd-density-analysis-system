from __future__ import annotations

import importlib.util
import queue
import random
import sys
import threading
from datetime import timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from environment_config import EnvironmentConfig
from shared_runtime import (
    FusedSensorData,
    InferenceAdapter,
    InferenceResult,
    ManagedWorker,
    ModelError,
    ResultMailbox,
    ThreadFailure,
)


DEBUG_RANDOM_PEOPLE_MIN = 0
DEBUG_RANDOM_PEOPLE_MAX = 50
DEBUG_RANDOM_CONFIDENCE = 0.0


class ModelAdapterWorker(ManagedWorker):
    def __init__(
        self,
        *,
        environment: EnvironmentConfig,
        fused_queue: queue.Queue[FusedSensorData],
        mailbox: ResultMailbox,
        stop_event: threading.Event,
        failure_queue: queue.Queue[ThreadFailure],
        debug: bool,
        verbose: bool,
        video: bool = False,
        adapter_loader: Callable[[Path, Path], InferenceAdapter] | None = None,
        random_people_count: Callable[[], int] | None = None,
    ) -> None:
        super().__init__(
            name="ModelAdapter",
            stop_event=stop_event,
            failure_queue=failure_queue,
            verbose=verbose,
        )
        self.environment = environment
        self.fused_queue = fused_queue
        self.mailbox = mailbox
        self.debug = debug
        self.video = video
        self.adapter_loader = adapter_loader or load_model_adapter
        self.random_people_count = (
            random_people_count or generate_debug_people_count
        )
        self._random_fallback_enabled = False

    def run_worker(self) -> None:
        adapter = self._load_adapter()
        try:
            while not self.stop_event.is_set():
                try:
                    fused = self.fused_queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                if adapter is None:
                    if not self._random_fallback_enabled:
                        continue
                    result = parse_inference_output(
                        {
                            "people_count": self.random_people_count(),
                            "confidence": DEBUG_RANDOM_CONFIDENCE,
                        },
                        fused,
                        self.environment,
                    )
                else:
                    self.verbose_info("running crowd inference model")
                    output = adapter.infer(
                        to_model_input(
                            fused,
                            debug=self.debug,
                            debug_dir=self.environment.fusion.debug_dir,
                            lidar_image_size=(
                                self.environment.fusion.lidar_image_size
                            ),
                            lidar_max_distance_m=(
                                self.environment.fusion.lidar_max_distance_m
                            ),
                            video=self.video,
                        )
                    )
                    if (
                        isinstance(output, dict)
                        and output.get("_stop_requested") is True
                    ):
                        self.stop_event.set()
                        return
                    result = parse_inference_output(
                        output, fused, self.environment
                    )
                if not self.mailbox.publish(result, self.stop_event):
                    return
                if adapter is None:
                    self.verbose_info(
                        "debug mode: random inference queued for server "
                        "people_count=%d confidence=%.3f",
                        result.people_count,
                        result.confidence,
                    )
        finally:
            if adapter is not None:
                close_adapter = getattr(adapter, "close", None)
                if callable(close_adapter):
                    close_adapter()

    def _load_adapter(self) -> InferenceAdapter | None:
        model = self.environment.model
        self._random_fallback_enabled = False
        if model.adapter_module is None:
            if self.debug:
                self._random_fallback_enabled = True
                self.verbose_info(
                    "debug mode: model adapter module is not configured; "
                    "random inference fallback is enabled"
                )
                return None
            raise ModelError("model.adapter_module is required")
        if not model.adapter_module.is_file():
            if self.debug:
                self._random_fallback_enabled = True
                self.verbose_info(
                    "debug mode: model adapter module was not found; "
                    "random inference fallback is enabled"
                )
                return None
            raise ModelError("configured model adapter module does not exist")
        if model.model_path is None:
            if self.debug:
                self.verbose_info(
                    "debug mode: model path is not configured; inference is skipped"
                )
                return None
            raise ModelError("model.model_path is required")
        if not model.model_path.is_file():
            if self.debug:
                self.verbose_info(
                    "debug mode: model file was not found; inference is skipped"
                )
                return None
            raise ModelError("configured model file does not exist")
        return self.adapter_loader(model.adapter_module, model.model_path)


def generate_debug_people_count() -> int:
    return random.randint(DEBUG_RANDOM_PEOPLE_MIN, DEBUG_RANDOM_PEOPLE_MAX)


def load_model_adapter(adapter_path: Path, model_path: Path) -> InferenceAdapter:
    spec = importlib.util.spec_from_file_location("cdas_model_adapter", adapter_path)
    if spec is None or spec.loader is None:
        raise ModelError(f"failed to load model adapter module: {adapter_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        execute_module(spec.loader, module)
    except Exception:
        if sys.modules.get(spec.name) is module:
            del sys.modules[spec.name]
        raise
    adapter_class = getattr(module, "ModelAdapter", None)
    if adapter_class is None or not callable(adapter_class):
        raise ModelError("model adapter module must define ModelAdapter")
    try:
        adapter = adapter_class(model_path)
    except Exception as exc:
        raise ModelError(f"failed to initialize model adapter: {exc}") from exc
    if not callable(getattr(adapter, "infer", None)):
        raise ModelError("ModelAdapter must define infer(sensor_data)")
    return adapter


def execute_module(loader: Any, module: ModuleType) -> None:
    try:
        loader.exec_module(module)
    except Exception as exc:
        raise ModelError(f"failed to import model adapter: {exc}") from exc


def to_model_input(
    fused: FusedSensorData,
    *,
    debug: bool = False,
    debug_dir: Path | None = None,
    lidar_image_size: int = 640,
    lidar_max_distance_m: float = 12.0,
    video: bool = False,
) -> dict[str, Any]:
    return {
        "inference_mode": "video" if video else "image",
        "fused_at": fused.fused_at.isoformat(),
        "thermal": {
            "captured_at": fused.thermal.captured_at.isoformat(),
            "width": fused.thermal.width,
            "height": fused.thermal.height,
            "pixels": fused.thermal.pixels,
        },
        "lidar": {
            "captured_at": fused.lidar.captured_at.isoformat(),
            "sequence": fused.lidar.sequence,
            "points": fused.lidar.points,
        },
        "debug": {
            "enabled": debug,
            "output_dir": str(debug_dir) if debug_dir is not None else None,
            "lidar_image_size": lidar_image_size,
            "lidar_max_distance_m": lidar_max_distance_m,
        },
    }


def parse_inference_output(
    output: dict[str, Any],
    fused: FusedSensorData,
    environment: EnvironmentConfig,
) -> InferenceResult:
    if not isinstance(output, dict):
        raise ModelError("model output must be a dictionary")
    people_count = output.get("people_count")
    confidence = output.get("confidence")
    if (
        isinstance(people_count, bool)
        or not isinstance(people_count, int)
        or people_count < 0
    ):
        raise ModelError("model output people_count must be a non-negative integer")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= float(confidence) <= 1
    ):
        raise ModelError("model output confidence must be between 0 and 1")
    return InferenceResult(
        node_id=environment.node.node_id,
        location_id=environment.node.location_id,
        timestamp=fused.fused_at.astimezone(timezone.utc),
        people_count=people_count,
        confidence=float(confidence),
    )
