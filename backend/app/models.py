from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


SUPPORTED_SENSOR_TYPES = {"thermal", "lidar"}


class ValidationError(ValueError):
    """Raised when an incoming sensor reading is invalid."""


@dataclass(frozen=True)
class SensorReading:
    device_id: str
    location_id: str
    sensor_type: str
    timestamp: datetime
    metrics: dict[str, Any]
    received_at: datetime

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SensorReading":
        if not isinstance(payload, dict):
            raise ValidationError("payload must be a JSON object")

        device_id = _required_string(payload, "device_id")
        location_id = _required_string(payload, "location_id")
        sensor_type = _required_string(payload, "sensor_type")
        if sensor_type not in SUPPORTED_SENSOR_TYPES:
            raise ValidationError(
                f"sensor_type must be one of {sorted(SUPPORTED_SENSOR_TYPES)}"
            )

        timestamp_raw = _required_string(payload, "timestamp")
        timestamp = parse_timestamp(timestamp_raw)

        metrics = payload.get("metrics")
        if not isinstance(metrics, dict):
            raise ValidationError("metrics must be a JSON object")
        validate_metrics(sensor_type, metrics)

        return cls(
            device_id=device_id,
            location_id=location_id,
            sensor_type=sensor_type,
            timestamp=timestamp,
            metrics=metrics,
            received_at=datetime.now(timezone.utc),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "location_id": self.location_id,
            "sensor_type": self.sensor_type,
            "timestamp": self.timestamp.isoformat(),
            "metrics": self.metrics,
            "received_at": self.received_at.isoformat(),
        }


def parse_timestamp(value: str) -> datetime:
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValidationError("timestamp must be ISO 8601 format") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{key} must be a non-empty string")
    return value.strip()


def validate_metrics(sensor_type: str, metrics: dict[str, Any]) -> None:
    if sensor_type == "thermal":
        _optional_number(metrics, "hotspot_count")
        _optional_number(metrics, "avg_temp")
        _optional_number(metrics, "max_temp")
        _optional_bool(metrics, "valid")
        return

    if sensor_type == "lidar":
        _optional_number(metrics, "object_count")
        _optional_number(metrics, "avg_distance")
        _optional_number(metrics, "min_distance")
        _optional_bool(metrics, "valid")
        return

    raise ValidationError(f"unsupported sensor_type: {sensor_type}")


def metric_bool(metrics: dict[str, Any], key: str, *, default: bool = True) -> bool:
    value = metrics.get(key, default)
    if not isinstance(value, bool):
        raise ValidationError(f"metrics.{key} must be a boolean")
    return value


def _optional_number(metrics: dict[str, Any], key: str) -> None:
    value = metrics.get(key)
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"metrics.{key} must be a number")


def _optional_bool(metrics: dict[str, Any], key: str) -> None:
    value = metrics.get(key)
    if value is None:
        return
    if not isinstance(value, bool):
        raise ValidationError(f"metrics.{key} must be a boolean")
