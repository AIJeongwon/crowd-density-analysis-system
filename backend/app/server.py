from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .models import SensorReading, ValidationError
from .scoring import build_location_status
from .store import ReadingStore


STORE = ReadingStore()


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

        if path_parts == ["api", "readings", "recent"]:
            limit = _parse_int(query.get("limit", ["20"])[0], default=20)
            sensor_type = query.get("sensor_type", [None])[0]
            location_id = query.get("location_id", [None])[0]
            readings = STORE.recent(
                location_id=location_id,
                sensor_type=sensor_type,
                limit=max(1, min(limit, 100)),
            )
            self._send_json(200, {"readings": [reading.to_dict() for reading in readings]})
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
                STORE.all(),
                window_seconds=max(1, min(window_seconds, 3600)),
            )
            code = 200 if status["status"] == "OK" else 404
            self._send_json(code, status)
            return

        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path != "/api/sensor-readings":
            self._send_json(404, {"error": "not_found"})
            return

        try:
            payload = self._read_json_body()
            reading = SensorReading.from_payload(payload)
        except ValidationError as exc:
            self._send_json(400, {"error": "validation_error", "message": str(exc)})
            return
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
            return

        STORE.add(reading)
        self._send_json(201, {"reading": reading.to_dict()})

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


def run(host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), RequestHandler)
    print(f"CDAS backend listening on http://{host}:{port}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CDAS prototype backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    run(args.host, args.port)


def _parse_int(value: str, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    main()
