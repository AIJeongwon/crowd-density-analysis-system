from __future__ import annotations

import logging
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol


THERMAL_WIDTH = 160
THERMAL_HEIGHT = 120
THERMAL_PIXEL_COUNT = THERMAL_WIDTH * THERMAL_HEIGHT
THERMAL_FRAME_BYTES = THERMAL_PIXEL_COUNT * 2
Y16_FOURCC = "0x20363159"

LOGGER = logging.getLogger("cdas.sensor_client")
LOG_FORMAT = (
    "%(asctime)s.%(msecs)03d %(levelname)s "
    "[%(threadName)s] %(message)s"
)
LOG_DATE_FORMAT = "%y-%m-%d %H:%M:%S"


class SensorClientError(RuntimeError):
    """Base error for managed sensor-client workers."""


class SensorError(SensorClientError):
    """Raised when a sensor cannot produce a valid sample."""


class ModelError(SensorClientError):
    """Raised when the model cannot be loaded or returns invalid output."""


class CommunicationError(SensorClientError):
    """Raised when communication repeatedly fails."""


@dataclass(frozen=True)
class ThermalFrame:
    captured_at: datetime
    pixels: tuple[int, ...]
    width: int = THERMAL_WIDTH
    height: int = THERMAL_HEIGHT


@dataclass(frozen=True)
class LidarScan:
    captured_at: datetime
    sequence: int
    points: tuple[tuple[float, float, int], ...]


@dataclass(frozen=True)
class FusedSensorData:
    fused_at: datetime
    thermal: ThermalFrame
    lidar: LidarScan


@dataclass(frozen=True)
class InferenceResult:
    node_id: str
    location_id: str
    timestamp: datetime
    people_count: int
    confidence: float

    def to_payload(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "location_id": self.location_id,
            "timestamp": self.timestamp.astimezone(timezone.utc).isoformat(),
            "people_count": self.people_count,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ThreadFailure:
    thread_name: str
    message: str


class InferenceAdapter(Protocol):
    def infer(self, sensor_data: dict[str, Any]) -> dict[str, Any]: ...


class ResultMailbox:
    """A lossless, single-slot handoff between adapter and communication."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._value: InferenceResult | None = None
        self._closed = False

    def publish(
        self,
        value: InferenceResult,
        stop_event: threading.Event,
    ) -> bool:
        with self._condition:
            while self._value is not None and not self._closed:
                if stop_event.is_set():
                    return False
                self._condition.wait(timeout=0.25)
            if self._closed or stop_event.is_set():
                return False
            self._value = value
            self._condition.notify_all()
            return True

    def take(
        self,
        timeout: float,
        stop_event: threading.Event,
    ) -> InferenceResult | None:
        deadline = time.monotonic() + max(timeout, 0.0)
        with self._condition:
            while self._value is None and not self._closed:
                if stop_event.is_set():
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=min(remaining, 0.25))
            value = self._value
            self._value = None
            if value is not None:
                self._condition.notify_all()
            return value

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


class ManagedWorker(threading.Thread):
    def __init__(
        self,
        *,
        name: str,
        stop_event: threading.Event,
        failure_queue: queue.Queue[ThreadFailure],
        verbose: bool,
    ) -> None:
        super().__init__(name=name, daemon=False)
        self.stop_event = stop_event
        self.failure_queue = failure_queue
        self.verbose = verbose

    def run(self) -> None:
        try:
            self.run_worker()
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            LOGGER.error(message)
            self.failure_queue.put(ThreadFailure(self.name, message))
            self.stop_event.set()

    def run_worker(self) -> None:
        raise NotImplementedError

    def verbose_info(self, message: str, *args: object) -> None:
        if self.verbose:
            LOGGER.info(message, *args)


def put_with_stop(
    target_queue: queue.Queue[Any],
    value: Any,
    stop_event: threading.Event,
) -> bool:
    while not stop_event.is_set():
        try:
            target_queue.put(value, timeout=0.25)
            return True
        except queue.Full:
            continue
    return False


def drain_queue(target_queue: queue.Queue[Any]) -> int:
    count = 0
    while True:
        try:
            target_queue.get_nowait()
            count += 1
        except queue.Empty:
            return count


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def decode_process_output(output: bytes | str | None) -> str:
    if output is None:
        return "no diagnostic output"
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace").strip()
    return output.strip()


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
    )


def python_is_little_endian() -> bool:
    return sys.byteorder == "little"
