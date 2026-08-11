from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .models import InferenceResult, LocationConfig


DEFAULT_WINDOW_SECONDS = 30


def build_location_status(
    location_id: str,
    results: list[InferenceResult],
    location_config: LocationConfig | None,
    *,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    window_start = now - timedelta(seconds=window_seconds)

    if location_config is None:
        return _empty_status(
            location_id,
            status="LOCATION_NOT_CONFIGURED",
            window_seconds=window_seconds,
        )

    latest: InferenceResult | None = None
    for result in results:
        if result.location_id != location_id:
            continue
        if result.received_at < window_start:
            continue
        if latest is None or result.received_at > latest.received_at:
            latest = result

    if latest is None:
        status = _empty_status(
            location_id,
            status="NO_DATA",
            window_seconds=window_seconds,
        )
        status.update(
            area_m2=location_config.area_m2,
            capacity=location_config.capacity,
        )
        return status

    density_per_m2 = latest.people_count / location_config.area_m2
    occupancy_ratio = latest.people_count / location_config.capacity
    congestion_score = round(min(occupancy_ratio * 100, 100.0), 1)

    return {
        "location_id": location_id,
        "status": "OK",
        "node_id": latest.node_id,
        "measured_at": latest.timestamp.isoformat(),
        "received_at": latest.received_at.isoformat(),
        "people_count": latest.people_count,
        "area_m2": location_config.area_m2,
        "capacity": location_config.capacity,
        "density_per_m2": round(density_per_m2, 4),
        "occupancy_ratio": round(occupancy_ratio, 4),
        "congestion_score": congestion_score,
        "congestion_level": _level(congestion_score),
        "confidence": latest.confidence,
        "window_seconds": window_seconds,
    }


def _level(score: float) -> str:
    if score < 35:
        return "LOW"
    if score < 70:
        return "MEDIUM"
    return "HIGH"


def _empty_status(
    location_id: str,
    *,
    status: str,
    window_seconds: int,
) -> dict[str, Any]:
    return {
        "location_id": location_id,
        "status": status,
        "node_id": None,
        "measured_at": None,
        "received_at": None,
        "people_count": None,
        "area_m2": None,
        "capacity": None,
        "density_per_m2": None,
        "occupancy_ratio": None,
        "congestion_score": None,
        "congestion_level": "UNKNOWN",
        "confidence": 0.0,
        "window_seconds": window_seconds,
    }
