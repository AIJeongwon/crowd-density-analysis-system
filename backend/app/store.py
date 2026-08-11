from __future__ import annotations

from collections import deque
from threading import Lock

from .models import InferenceResult, SensorReading


class InferenceStore:
    def __init__(self, max_results: int = 1000) -> None:
        if isinstance(max_results, bool) or not isinstance(max_results, int):
            raise TypeError("max_results must be an integer")
        if max_results <= 0:
            raise ValueError("max_results must be positive")
        self._results: deque[InferenceResult] = deque(maxlen=max_results)
        self._lock = Lock()

    def add(self, result: InferenceResult) -> None:
        with self._lock:
            self._results.append(result)

    def all(self) -> list[InferenceResult]:
        with self._lock:
            return list(self._results)

    def recent(
        self,
        *,
        location_id: str | None = None,
        node_id: str | None = None,
        limit: int = 20,
    ) -> list[InferenceResult]:
        with self._lock:
            results = list(self._results)

        filtered: list[InferenceResult] = []
        for result in reversed(results):
            if location_id is not None and result.location_id != location_id:
                continue
            if node_id is not None and result.node_id != node_id:
                continue
            filtered.append(result)
            if len(filtered) >= limit:
                break
        return filtered


class ReadingStore:
    def __init__(self, max_readings: int = 1000) -> None:
        self._readings: deque[SensorReading] = deque(maxlen=max_readings)
        self._lock = Lock()

    def add(self, reading: SensorReading) -> None:
        with self._lock:
            self._readings.append(reading)

    def all(self) -> list[SensorReading]:
        with self._lock:
            return list(self._readings)

    def recent(
        self,
        *,
        location_id: str | None = None,
        sensor_type: str | None = None,
        limit: int = 20,
    ) -> list[SensorReading]:
        with self._lock:
            readings = list(self._readings)

        filtered: list[SensorReading] = []
        for reading in reversed(readings):
            if location_id is not None and reading.location_id != location_id:
                continue
            if sensor_type is not None and reading.sensor_type != sensor_type:
                continue
            filtered.append(reading)
            if len(filtered) >= limit:
                break
        return filtered
