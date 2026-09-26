from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_ENVIRONMENT_PATH = Path(__file__).resolve().with_name("environment.json")


class ConfigurationError(ValueError):
    """Raised when environment.json is missing or invalid."""


@dataclass(frozen=True)
class NodeConfig:
    node_id: str
    location_id: str


@dataclass(frozen=True)
class ThermalConfig:
    device_path: str
    capture_interval_seconds: float
    capture_timeout_seconds: float


@dataclass(frozen=True)
class LidarConfig:
    bridge_path: Path
    device_path: str
    baud_rate: int
    scan_mode: str
    timeout_ms: int


@dataclass(frozen=True)
class FusionConfig:
    poll_interval_seconds: float
    flush_every_checks: int
    sensor_queue_size: int
    fused_queue_size: int
    debug_dir: Path
    lidar_image_size: int
    lidar_max_distance_m: float


@dataclass(frozen=True)
class ModelConfig:
    adapter_module: Path | None
    model_path: Path | None
    video_inference_fps: float = 4.0


@dataclass(frozen=True)
class ServerConfig:
    base_url: str
    request_timeout_seconds: float
    heartbeat_interval_seconds: float
    heartbeat_warning_seconds: float
    max_consecutive_failures: int
    api_token: str | None = None


@dataclass(frozen=True)
class RuntimeConfig:
    worker_restart_delay_seconds: float = 5.0


@dataclass(frozen=True)
class EnvironmentConfig:
    node: NodeConfig
    thermal: ThermalConfig
    lidar: LidarConfig
    fusion: FusionConfig
    model: ModelConfig
    server: ServerConfig
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)


def load_environment(
    path: Path = DEFAULT_ENVIRONMENT_PATH,
) -> EnvironmentConfig:
    path = path.resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"environment file not found: {path}") from exc
    except OSError as exc:
        raise ConfigurationError(f"failed to read environment file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"environment file contains invalid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ConfigurationError("environment root must be a JSON object")
    base_dir = path.parent

    node = _section(payload, "node")
    thermal = _section(payload, "thermal")
    lidar = _section(payload, "lidar")
    fusion = _section(payload, "fusion")
    model = _section(payload, "model")
    server = _section(payload, "server")
    runtime = _section(payload, "runtime") if "runtime" in payload else {}

    return EnvironmentConfig(
        node=NodeConfig(
            node_id=_string(node, "node_id"),
            location_id=_string(node, "location_id"),
        ),
        thermal=ThermalConfig(
            device_path=_string(thermal, "device_path"),
            capture_interval_seconds=_positive_float(
                thermal, "capture_interval_seconds"
            ),
            capture_timeout_seconds=_positive_float(
                thermal, "capture_timeout_seconds"
            ),
        ),
        lidar=LidarConfig(
            bridge_path=_relative_path(base_dir, _string(lidar, "bridge_path")),
            device_path=_string(lidar, "device_path"),
            baud_rate=_positive_int(lidar, "baud_rate"),
            scan_mode=_string(lidar, "scan_mode"),
            timeout_ms=_positive_int(lidar, "timeout_ms"),
        ),
        fusion=FusionConfig(
            poll_interval_seconds=_positive_float(
                fusion, "poll_interval_seconds"
            ),
            flush_every_checks=_positive_int(fusion, "flush_every_checks"),
            sensor_queue_size=_positive_int(fusion, "sensor_queue_size"),
            fused_queue_size=_positive_int(fusion, "fused_queue_size"),
            debug_dir=_relative_path(base_dir, _string(fusion, "debug_dir")),
            lidar_image_size=_positive_int(fusion, "lidar_image_size"),
            lidar_max_distance_m=_positive_float(
                fusion, "lidar_max_distance_m"
            ),
        ),
        model=ModelConfig(
            adapter_module=_optional_path(base_dir, model, "adapter_module"),
            model_path=_optional_path(base_dir, model, "model_path"),
            video_inference_fps=_positive_float_or_default(
                model, "video_inference_fps", 4.0
            ),
        ),
        server=ServerConfig(
            base_url=_http_url(_string(server, "base_url")),
            request_timeout_seconds=_positive_float(
                server, "request_timeout_seconds"
            ),
            heartbeat_interval_seconds=_positive_float(
                server, "heartbeat_interval_seconds"
            ),
            heartbeat_warning_seconds=_positive_float(
                server, "heartbeat_warning_seconds"
            ),
            max_consecutive_failures=_positive_int(
                server, "max_consecutive_failures"
            ),
            api_token=_optional_api_token(server, "api_token"),
        ),
        runtime=RuntimeConfig(
            worker_restart_delay_seconds=_positive_float_or_default(
                runtime, "worker_restart_delay_seconds", 5.0
            ),
        ),
    )


def _section(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ConfigurationError(f"{key} must be a JSON object")
    return value


def _string(section: dict[str, Any], key: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{key} must be a non-empty string")
    return value.strip()


def _positive_float(section: dict[str, Any], key: str) -> float:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{key} must be a positive number")
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as exc:
        raise ConfigurationError(f"{key} must be a positive number") from exc
    if not math.isfinite(normalized) or normalized <= 0:
        raise ConfigurationError(f"{key} must be a positive number")
    return normalized


def _positive_float_or_default(
    section: dict[str, Any],
    key: str,
    default: float,
) -> float:
    if key not in section:
        return default
    return _positive_float(section, key)


def _positive_int(section: dict[str, Any], key: str) -> int:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigurationError(f"{key} must be a positive integer")
    return value


def _relative_path(base_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _optional_path(
    base_dir: Path,
    section: dict[str, Any],
    key: str,
) -> Path | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigurationError(f"{key} must be a string or null")
    value = value.strip()
    if not value:
        return None
    return _relative_path(base_dir, value)


def _http_url(value: str) -> str:
    normalized = value.rstrip("/")
    if not normalized.startswith(("http://", "https://")):
        raise ConfigurationError("server.base_url must start with http:// or https://")
    return normalized


def _optional_api_token(
    section: dict[str, Any],
    key: str,
) -> str | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigurationError(f"server.{key} must be a string or null")
    if not value or any(
        ord(character) < 0x21 or ord(character) > 0x7E
        for character in value
    ):
        raise ConfigurationError(
            f"server.{key} must contain visible ASCII characters without whitespace"
        )
    return value
