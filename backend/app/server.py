from __future__ import annotations

import argparse
import json
import threading
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from .models import (
    InferenceResult,
    LocationConfig,
    ValidationError,
    normalize_location_configs,
)
from .scoring import build_location_status
from .store import InferenceStore


DEFAULT_ENVIRONMENT_PATH = Path(__file__).resolve().with_name("environment.json")


@dataclass(frozen=True)
class ServerEnvironment:
    host: str
    port: int
    location_configs: dict[str, LocationConfig]


def load_server_environment(path: str | Path) -> ServerEnvironment:
    environment_path = Path(path)
    try:
        text = environment_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValidationError(
            f"cannot read {environment_path}: {exc}"
        ) from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"invalid JSON in {environment_path}: "
            f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(payload, dict):
        raise ValidationError("environment root must be a JSON object")

    backend = payload.get("backend")
    if not isinstance(backend, dict):
        raise ValidationError("backend must be a JSON object")

    host = backend.get("host")
    if not isinstance(host, str) or not host.strip():
        raise ValidationError("backend.host must be a non-empty string")
    host = host.strip()

    port = backend.get("port")
    if (
        isinstance(port, bool)
        or not isinstance(port, int)
        or not 1 <= port <= 65535
    ):
        raise ValidationError(
            "backend.port must be an integer between 1 and 65535"
        )

    locations = payload.get("locations")
    if not isinstance(locations, dict):
        raise ValidationError("locations must be a JSON object")

    return ServerEnvironment(
        host=host,
        port=port,
        location_configs=normalize_location_configs(locations),
    )


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "CDASPrototype/0.1"

    def do_OPTIONS(self) -> None:
        self._send_empty(204)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path_parts = [part for part in parsed.path.split("/") if part]
        query = parse_qs(parsed.query)

        if parsed.path == "/health":
            self._send_json(200, {"status": "ok"})
            return

        if path_parts == ["api", "inference-results", "recent"]:
            limit = _parse_int(query.get("limit", ["20"])[0], default=20)
            location_id = query.get("location_id", [None])[0]
            node_id = query.get("node_id", [None])[0]
            results = self._result_store().recent(
                location_id=location_id,
                node_id=node_id,
                limit=max(1, min(limit, 100)),
            )
            self._send_json(200, {"results": [result.to_dict() for result in results]})
            return

        if (
            len(path_parts) == 4
            and path_parts[0] == "api"
            and path_parts[1] == "locations"
            and path_parts[3] == "status"
        ):
            location_id = path_parts[2]
            window_seconds = _parse_int(query.get("window_seconds", ["30"])[0], default=30)
            status = build_location_status(
                location_id,
                self._result_store().all(),
                self._location_configs().get(location_id),
                window_seconds=max(1, min(window_seconds, 3600)),
            )
            code = 200 if status["status"] == "OK" else 404
            self._send_json(code, status)
            return

        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path != "/api/inference-results":
            self._send_json(404, {"error": "not_found"})
            return

        try:
            payload = self._read_json_body()
            result = InferenceResult.from_payload(payload)
        except ValidationError as exc:
            self._send_json(400, {"error": "validation_error", "message": str(exc)})
            return
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
            return

        self._result_store().add(result)
        log_received_result(result)
        self._send_json(201, {"result": result.to_dict()})

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_json_body(self) -> dict:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValidationError("Content-Length must be an integer") from exc
        if content_length <= 0:
            raise ValidationError("request body is required")
        if content_length > 1_000_000:
            raise ValidationError("request body is too large")

        body = self.rfile.read(content_length)
        decoded = body.decode("utf-8")
        payload = json.loads(decoded)
        if not isinstance(payload, dict):
            raise ValidationError("request body must be a JSON object")
        return payload

    def _send_json(self, status_code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self._send_common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status_code: int) -> None:
        self.send_response(status_code)
        self._send_common_headers()
        self.end_headers()

    def _send_common_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _result_store(self) -> InferenceStore:
        return getattr(self.server, "result_store")

    def _location_configs(self) -> dict[str, LocationConfig]:
        return getattr(self.server, "location_configs")


def log_received_result(result: InferenceResult) -> None:
    write_log(
        "received inference result: "
        f"node_id={result.node_id} "
        f"location_id={result.location_id} "
        f"people_count={result.people_count} "
        f"confidence={result.confidence:.3f}",
        timestamp=result.received_at,
    )


def format_log_timestamp(timestamp: datetime | None = None) -> str:
    value = timestamp or datetime.now().astimezone()
    if value.tzinfo is not None:
        value = value.astimezone()
    return value.strftime("%y-%m-%d %H:%M:%S.%f")[:-3]


def write_log(
    message: str,
    *,
    timestamp: datetime | None = None,
    level: str = "INFO",
) -> None:
    print(
        f"{format_log_timestamp(timestamp)} {level} "
        f"[{threading.current_thread().name}] {message}",
        flush=True,
    )


def create_server(
    host: str,
    port: int,
    *,
    store: InferenceStore | None = None,
    location_configs: Mapping[
        str,
        LocationConfig | Mapping[str, Any],
    ]
    | None = None,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), RequestHandler)
    server.result_store = store or InferenceStore()  # type: ignore[attr-defined]
    server.location_configs = normalize_location_configs(  # type: ignore[attr-defined]
        location_configs
    )
    return server


def run(
    host: str,
    port: int,
    *,
    location_configs: Mapping[
        str,
        LocationConfig | Mapping[str, Any],
    ]
    | None = None,
) -> None:
    with create_server(host, port, location_configs=location_configs) as server:
        write_log(f"CDAS backend listening on http://{host}:{port}")
        server.serve_forever()


def main() -> None:
    parser = build_argument_parser()
    parser.parse_args()
    try:
        environment = load_server_environment(DEFAULT_ENVIRONMENT_PATH)
    except ValidationError as exc:
        parser.error(str(exc))

    run(
        environment.host,
        environment.port,
        location_configs=environment.location_configs,
    )


def build_argument_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Run the CDAS prototype backend.")


def _parse_int(value: str, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    main()
