from __future__ import annotations

from collections import deque
from threading import Lock

from .models import SensorReading


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
