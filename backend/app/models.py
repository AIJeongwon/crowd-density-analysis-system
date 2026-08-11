from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


SUPPORTED_SENSOR_TYPES = {"thermal", "lidar"}


class ValidationError(ValueError):
    """Raised when an incoming payload or configuration is invalid."""


@dataclass(frozen=True)
class InferenceResult:
    node_id: str
    location_id: str
    timestamp: datetime
    people_count: int
    confidence: float
    received_at: datetime

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "InferenceResult":
        if not isinstance(payload, dict):
            raise ValidationError("payload must be a JSON object")

        node_id = _required_string(payload, "node_id")
        location_id = _required_string(payload, "location_id")
        timestamp = parse_timestamp(_required_string(payload, "timestamp"))

        people_count = payload.get("people_count")
        if (
            isinstance(people_count, bool)
            or not isinstance(people_count, int)
            or people_count < 0
        ):
            raise ValidationError("people_count must be a non-negative integer")

        confidence = payload.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ValidationError("confidence must be a number between 0 and 1")

        return cls(
            node_id=node_id,
            location_id=location_id,
            timestamp=timestamp,
            people_count=people_count,
            confidence=float(confidence),
            received_at=datetime.now(timezone.utc),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "location_id": self.location_id,
            "timestamp": self.timestamp.isoformat(),
            "people_count": self.people_count,
            "confidence": self.confidence,
            "received_at": self.received_at.isoformat(),
        }


@dataclass(frozen=True)
class LocationConfig:
    location_id: str
    area_m2: float
    capacity: int

    @classmethod
    def from_mapping(
        cls,
        location_id: str,
        payload: Mapping[str, Any],
    ) -> "LocationConfig":
        if not isinstance(payload, Mapping):
            raise ValidationError(f"locations.{location_id} must be a JSON object")

        normalized_id = location_id.strip() if isinstance(location_id, str) else ""
        if not normalized_id:
            raise ValidationError("location_id must be a non-empty string")

        area_m2 = payload.get("area_m2")
        if (
            isinstance(area_m2, bool)
            or not isinstance(area_m2, (int, float))
            or not math.isfinite(area_m2)
            or area_m2 <= 0
        ):
            raise ValidationError(
                f"locations.{normalized_id}.area_m2 must be a positive number"
            )

        capacity = payload.get("capacity")
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity <= 0
        ):
            raise ValidationError(
                f"locations.{normalized_id}.capacity must be a positive integer"
            )

        return cls(
            location_id=normalized_id,
            area_m2=float(area_m2),
            capacity=capacity,
        )


def normalize_location_configs(
    locations: Mapping[str, LocationConfig | Mapping[str, Any]] | None,
) -> dict[str, LocationConfig]:
    if locations is None:
        return {}
    if not isinstance(locations, Mapping):
        raise ValidationError("locations must be a JSON object")

    normalized: dict[str, LocationConfig] = {}
    for location_id, value in locations.items():
        if isinstance(value, LocationConfig):
            config = value
            if config.location_id != location_id:
                raise ValidationError(
                    f"location key {location_id!r} does not match "
                    f"LocationConfig.location_id {config.location_id!r}"
                )
        else:
            config = LocationConfig.from_mapping(location_id, value)
        normalized[config.location_id] = config
    return normalized


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
