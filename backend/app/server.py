from __future__ import annotations

import argparse
import json
import re
import struct
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
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
        process_received_reading(
            reading,
            logging_enabled=bool(getattr(self.server, "logging_enabled", False)),
            log_dir=Path(getattr(self.server, "log_dir", Path(".log_data"))),
        )
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


def log_received_reading(reading: SensorReading) -> None:
    print(
        f"[{reading.received_at.isoformat()}] received sensor reading: "
        f"device_id={reading.device_id} "
        f"location_id={reading.location_id} "
        f"sensor_type={reading.sensor_type} "
        f"timestamp={reading.timestamp.isoformat()}",
        flush=True,
    )


def process_received_reading(
    reading: SensorReading,
    *,
    logging_enabled: bool = False,
    log_dir: Path = Path(".log_data"),
) -> Path | None:
    log_received_reading(reading)
    if not logging_enabled:
        return None

    try:
        image_path = save_thermal_image(reading, log_dir=log_dir)
    except (OSError, ValueError) as exc:
        print(f"failed to save thermal image: {exc}", flush=True)
        return None

    print(f"saved thermal image: {image_path}", flush=True)
    return image_path


def save_thermal_image(
    reading: SensorReading,
    *,
    log_dir: Path = Path(".log_data"),
) -> Path:
    if reading.sensor_type != "thermal":
        raise ValueError("image logging is only supported for thermal readings")

    width = reading.metrics.get("width", 160)
    height = reading.metrics.get("height", 120)
    pixels = reading.metrics.get("pixels")
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        raise ValueError("metrics.width must be a positive integer")
    if isinstance(height, bool) or not isinstance(height, int) or height <= 0:
        raise ValueError("metrics.height must be a positive integer")
    if not isinstance(pixels, list) or len(pixels) != width * height:
        raise ValueError(f"metrics.pixels must contain {width * height} values")
    if any(
        isinstance(pixel, bool) or not isinstance(pixel, (int, float))
        for pixel in pixels
    ):
        raise ValueError("metrics.pixels must contain only numbers")

    minimum = min(pixels)
    maximum = max(pixels)
    if maximum == minimum:
        intensities = bytes(len(pixels))
    else:
        scale = 255.0 / (maximum - minimum)
        intensities = bytes(
            max(0, min(255, round((pixel - minimum) * scale)))
            for pixel in pixels
        )

    rgb_pixels = bytearray()
    for intensity in intensities:
        rgb_pixels.extend(_thermal_rgb(intensity))
    row_stride = width * 3
    scanlines = b"".join(
        b"\x00" + rgb_pixels[row_start : row_start + row_stride]
        for row_start in range(0, len(rgb_pixels), row_stride)
    )
    png = b"\x89PNG\r\n\x1a\n"
    png += _png_chunk(
        b"IHDR",
        struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
    )
    png += _png_chunk(b"IDAT", zlib.compress(scanlines))
    png += _png_chunk(b"IEND", b"")

    log_dir.mkdir(parents=True, exist_ok=True)
    safe_device_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", reading.device_id)
    safe_device_id = safe_device_id.strip("._") or "thermal-sensor"
    timestamp = reading.received_at.strftime("%Y%m%dT%H%M%S_%fZ")
    image_path = log_dir / f"{timestamp}_{safe_device_id}.png"
    temporary_path = image_path.with_suffix(".png.part")
    temporary_path.write_bytes(png)
    temporary_path.replace(image_path)
    return image_path


def _thermal_rgb(intensity: int) -> tuple[int, int, int]:
    palette = (
        (0, (0, 0, 0)),
        (32, (0, 0, 96)),
        (80, (72, 0, 160)),
        (128, (192, 0, 96)),
        (176, (255, 64, 0)),
        (224, (255, 200, 0)),
        (255, (255, 255, 255)),
    )
    for index in range(1, len(palette)):
        lower_value, lower_color = palette[index - 1]
        upper_value, upper_color = palette[index]
        if intensity <= upper_value:
            ratio = (intensity - lower_value) / (upper_value - lower_value)
            return tuple(
                round(lower + (upper - lower) * ratio)
                for lower, upper in zip(lower_color, upper_color)
            )
    return palette[-1][1]


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", checksum)
    )


def run(host: str, port: int, logging_enabled: bool = False) -> None:
    server = ThreadingHTTPServer((host, port), RequestHandler)
    server.logging_enabled = logging_enabled  # type: ignore[attr-defined]
    server.log_dir = Path(".log_data")  # type: ignore[attr-defined]
    mode = "logging" if logging_enabled else "normal"
    print(f"CDAS backend listening on http://{host}:{port} ({mode} mode)")
    server.serve_forever()


def main() -> None:
    parser = build_argument_parser()
    args = parser.parse_args()
    run(args.host, args.port, args.logging)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the CDAS prototype backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--logging",
        action="store_true",
        help="save received thermal frames as PNG",
    )
    return parser


def _parse_int(value: str, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    main()
