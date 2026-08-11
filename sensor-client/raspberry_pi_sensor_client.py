from __future__ import annotations

# Direct execution is a compatibility entry point for the sensor runtime.
if __name__ == "__main__":
    from sensor_client import main as _sensor_main

    raise SystemExit(_sensor_main())


import argparse
import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SUPPORTED_SENSOR_TYPES = {"thermal", "lidar"}
RETRYABLE_HTTP_STATUS_CODES = {408, 429}


class SensorClientError(RuntimeError):
    """Base error raised by the Raspberry Pi sensor client."""


class SendError(SensorClientError):
    """Raised when a sensor reading cannot be delivered to the server."""


class SensorClient:
    """Send preprocessed Raspberry Pi sensor metrics to the CDAS backend."""

    def __init__(
        self,
        server_url: str,
        device_id: str,
        location_id: str,
        *,
        timeout: float = 5.0,
        max_attempts: int = 3,
        retry_delay: float = 1.0,
        api_key: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.server_url = _required_text(server_url, "server_url").rstrip("/")
        self.device_id = _required_text(device_id, "device_id")
        self.location_id = _required_text(location_id, "location_id")
        if timeout <= 0:
            raise ValueError("timeout must be greater than 0")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if retry_delay < 0:
            raise ValueError("retry_delay must be 0 or greater")

        self.timeout = timeout
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay
        self.api_key = api_key.strip() if api_key and api_key.strip() else None
        self._sleep = sleep

    def send(
        self,
        sensor_type: str,
        metrics: dict[str, Any],
        *,
        measured_at: datetime | None = None,
    ) -> dict[str, Any]:
        payload = self.build_payload(sensor_type, metrics, measured_at=measured_at)
        body = _encode_json(payload)
        endpoint = self.server_url + "/api/sensor-readings"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "CDAS-RaspberryPi-SensorClient/1.0",
        }
        if self.api_key is not None:
            headers["X-API-Key"] = self.api_key

        request = Request(endpoint, data=body, method="POST", headers=headers)
        last_error: SendError | None = None

        for attempt in range(1, self.max_attempts + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    response_body = response.read()
                    status_code = response.getcode()
                if not 200 <= status_code < 300:
                    raise SendError(f"server returned HTTP {status_code}")
                return _decode_response(response_body)
            except HTTPError as exc:
                message = _http_error_message(exc)
                last_error = SendError(message)
                retryable = exc.code >= 500 or exc.code in RETRYABLE_HTTP_STATUS_CODES
                if not retryable:
                    raise last_error from exc
            except (URLError, TimeoutError, OSError) as exc:
                last_error = SendError(f"failed to reach {endpoint}: {exc}")

            if attempt < self.max_attempts:
                self._sleep(self.retry_delay * (2 ** (attempt - 1)))

        assert last_error is not None
        raise last_error

    def build_payload(
        self,
        sensor_type: str,
        metrics: dict[str, Any],
        *,
        measured_at: datetime | None = None,
    ) -> dict[str, Any]:
        if sensor_type not in SUPPORTED_SENSOR_TYPES:
            raise ValueError(
                f"sensor_type must be one of {sorted(SUPPORTED_SENSOR_TYPES)}"
            )
        if not isinstance(metrics, dict):
            raise TypeError("metrics must be a dictionary")

        timestamp = measured_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        payload = {
            "device_id": self.device_id,
            "location_id": self.location_id,
            "sensor_type": sensor_type,
            "timestamp": timestamp.astimezone(timezone.utc).isoformat(),
            "metrics": dict(metrics),
        }
        _encode_json(payload)
        return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Send Raspberry Pi sensor metrics to the CDAS backend. "
            "Pass one metrics JSON object with --metrics-json, or stream one "
            "JSON object per line through standard input."
        )
    )
    parser.add_argument(
        "--server-url",
        default=os.environ.get("CDAS_SERVER_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument("--location-id", required=True)
    parser.add_argument("--sensor-type", required=True, choices=sorted(SUPPORTED_SENSOR_TYPES))
    parser.add_argument(
        "--device-id",
        default=None,
        help="defaults to <hostname>-<sensor-type>",
    )
    parser.add_argument(
        "--metrics-json",
        default=None,
        help="send one metrics JSON object instead of reading JSON Lines from stdin",
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=1.0)
    parser.add_argument(
        "--api-key",
        default=os.environ.get("CDAS_API_KEY"),
        help="optional API key; defaults to the CDAS_API_KEY environment variable",
    )
    args = parser.parse_args()

    device_id = args.device_id or f"{socket.gethostname()}-{args.sensor_type}"
    try:
        client = SensorClient(
            args.server_url,
            device_id,
            args.location_id,
            timeout=args.timeout,
            max_attempts=args.max_attempts,
            retry_delay=args.retry_delay,
            api_key=args.api_key,
        )
        if args.metrics_json is not None:
            metrics = _parse_metrics(args.metrics_json, source="--metrics-json")
            _print_result(client.send(args.sensor_type, metrics))
            return 0

        if sys.stdin.isatty():
            parser.error("provide --metrics-json or pipe JSON Lines through stdin")

        for line_number, line in enumerate(sys.stdin, start=1):
            if not line.strip():
                continue
            metrics = _parse_metrics(line, source=f"stdin line {line_number}")
            _print_result(client.send(args.sensor_type, metrics))
        return 0
    except (SensorClientError, TypeError, ValueError) as exc:
        print(f"sensor client error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("sensor client stopped", file=sys.stderr)
        return 130


def _required_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _encode_json(payload: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"payload must contain valid JSON values: {exc}") from exc


def _decode_response(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SendError("server returned an invalid JSON response") from exc
    if not isinstance(payload, dict):
        raise SendError("server response must be a JSON object")
    return payload


def _http_error_message(exc: HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except OSError:
        body = ""
    detail = f": {body}" if body else ""
    return f"server returned HTTP {exc.code}{detail}"


def _parse_metrics(raw: str, *, source: str) -> dict[str, Any]:
    try:
        metrics = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source} must be valid JSON: {exc.msg}") from exc
    if not isinstance(metrics, dict):
        raise ValueError(f"{source} must be a JSON object")
    return metrics


def _print_result(result: dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
