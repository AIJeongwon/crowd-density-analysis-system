from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .models import SensorReading, ValidationError, metric_bool


DEFAULT_WINDOW_SECONDS = 30


def build_location_status(
    location_id: str,
    readings: list[SensorReading],
    *,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    window_start = now - timedelta(seconds=window_seconds)

    latest_by_type: dict[str, SensorReading] = {}
    for reading in readings:
        if reading.location_id != location_id:
            continue
        if reading.received_at < window_start:
            continue

        previous = latest_by_type.get(reading.sensor_type)
        if previous is None or reading.received_at > previous.received_at:
            latest_by_type[reading.sensor_type] = reading

    scores: list[tuple[str, float]] = []
    sensor_summaries: dict[str, dict[str, Any]] = {}

    thermal = latest_by_type.get("thermal")
    if thermal is not None:
        score = _thermal_score(thermal.metrics)
        scores.append(("thermal", score))
        sensor_summaries["thermal"] = _summarize_reading(thermal, score)

    lidar = latest_by_type.get("lidar")
    if lidar is not None:
        score = _lidar_score(lidar.metrics)
        scores.append(("lidar", score))
        sensor_summaries["lidar"] = _summarize_reading(lidar, score)

    if not scores:
        return {
            "location_id": location_id,
            "status": "NO_DATA",
            "congestion_score": None,
            "congestion_level": "UNKNOWN",
            "confidence": 0.0,
            "window_seconds": window_seconds,
            "sensors": {},
        }

    combined_score = round(sum(score for _, score in scores) / len(scores), 1)
    confidence = _confidence(latest_by_type, expected_sensor_count=2)

    return {
        "location_id": location_id,
        "status": "OK",
        "congestion_score": combined_score,
        "congestion_level": _level(combined_score),
        "confidence": confidence,
        "window_seconds": window_seconds,
        "sensors": sensor_summaries,
    }


def _thermal_score(metrics: dict[str, Any]) -> float:
    hotspot_count = _as_float(metrics.get("hotspot_count"), default=0)
    valid = _is_valid(metrics)
    score = min(max(hotspot_count, 0), 20) / 20 * 100
    return round(score if valid else score * 0.4, 1)


def _lidar_score(metrics: dict[str, Any]) -> float:
    object_count = _as_float(metrics.get("object_count"), default=0)
    avg_distance = _as_float(metrics.get("avg_distance"), default=5.0)
    valid = _is_valid(metrics)

    count_score = min(max(object_count, 0), 25) / 25 * 80
    distance_bonus = max(0.0, min(20.0, (5.0 - avg_distance) / 5.0 * 20))
    score = count_score + distance_bonus
    return round(score if valid else score * 0.4, 1)


def _confidence(readings_by_type: dict[str, SensorReading], expected_sensor_count: int) -> float:
    available = len(readings_by_type)
    if available == 0:
        return 0.0
    confidence = min(1.0, available / expected_sensor_count)
    if available == expected_sensor_count:
        confidence = 0.9
    return round(confidence, 2)


def _level(score: float) -> str:
    if score < 35:
        return "LOW"
    if score < 70:
        return "MEDIUM"
    return "HIGH"


def _summarize_reading(reading: SensorReading, score: float) -> dict[str, Any]:
    return {
        "device_id": reading.device_id,
        "sensor_type": reading.sensor_type,
        "measured_at": reading.timestamp.isoformat(),
        "received_at": reading.received_at.isoformat(),
        "score": score,
        "metrics": reading.metrics,
    }


def _as_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_valid(metrics: dict[str, Any]) -> bool:
    try:
        return metric_bool(metrics, "valid", default=True)
    except ValidationError:
        return False
