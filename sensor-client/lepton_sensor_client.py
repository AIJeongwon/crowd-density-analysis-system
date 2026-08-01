from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from array import array
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen


WIDTH = 160
HEIGHT = 120
PIXEL_COUNT = WIDTH * HEIGHT
BYTES_PER_PIXEL = 2
FRAME_BYTES = PIXEL_COUNT * BYTES_PER_PIXEL
Y16_FOURCC = "0x20363159"
DEFAULT_SERVER_PORT = 8000
DEFAULT_ENDPOINT_PATH = "/api/sensor-readings"
DEFAULT_WORK_DIR = Path("/tmp/rbp_client")


class LeptonClientError(RuntimeError):
    """Base error raised by the Lepton sensor client."""


class CaptureError(LeptonClientError):
    """Raised when v4l2-ctl cannot capture a valid Y16 frame."""


class UploadError(LeptonClientError):
    """Raised when a captured frame cannot be sent to the server."""


class LeptonSensorClient:
    """Capture Lepton 3.5 Y16 frames through a PureThermal UVC device."""

    def __init__(
        self,
        server_ip: str,
        dev_path: str,
        interval: float,
        verbose: bool,
        *,
        work_dir: Path = DEFAULT_WORK_DIR,
        request_timeout: float = 10.0,
        capture_timeout: float = 10.0,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        opener: Callable[..., Any] = urlopen,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be greater than 0 seconds")
        if request_timeout <= 0:
            raise ValueError("request_timeout must be greater than 0")
        if capture_timeout <= 0:
            raise ValueError("capture_timeout must be greater than 0")
        if not isinstance(dev_path, str) or not dev_path.strip():
            raise ValueError("dev_path must be a non-empty string")

        hostname = socket.gethostname()
        self.endpoint = normalize_server_endpoint(server_ip)
        self.dev_path = dev_path.strip()
        self.interval = float(interval)
        self.verbose = bool(verbose)
        self.work_dir = Path(work_dir)
        self.request_timeout = request_timeout
        self.capture_timeout = capture_timeout
        self.device_id = f"{hostname}-lepton-3.5"
        self.location_id = os.environ.get("CDAS_LOCATION_ID", hostname)
        self._runner = runner
        self._opener = opener
        self._sleep = sleep
        self._monotonic = monotonic

    def prepare(self) -> None:
        if shutil.which("v4l2-ctl") is None:
            raise CaptureError("v4l2-ctl was not found in PATH")

        device = Path(self.dev_path)
        if not device.exists():
            raise CaptureError(f"video device does not exist: {self.dev_path}")

        self.work_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.work_dir.chmod(0o700)
        except OSError:
            # Existing mount permissions may not support chmod. File permissions
            # still follow the process umask in that case.
            pass

    def capture_once(self) -> Path:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        captured_at = datetime.now(timezone.utc)
        sequence = time.time_ns()
        stem = f"lepton_{captured_at:%Y%m%dT%H%M%S}_{sequence}"
        partial_path = self.work_dir / f"{stem}.part"
        frame_path = self.work_dir / f"{stem}.y16"

        command = [
            "v4l2-ctl",
            f"--device={self.dev_path}",
            (
                "--set-fmt-video="
                f"width={WIDTH},height={HEIGHT},pixelformat={Y16_FOURCC}"
            ),
            "--stream-mmap=4",
            "--stream-count=1",
            f"--stream-to={partial_path}",
        ]

        self.log(f"capturing frame from {self.dev_path}")
        try:
            result = self._runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.capture_timeout,
            )
        except FileNotFoundError as exc:
            raise CaptureError("v4l2-ctl was not found in PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise CaptureError(
                f"v4l2-ctl timed out after {self.capture_timeout:g} seconds"
            ) from exc
        except OSError as exc:
            raise CaptureError(f"failed to execute v4l2-ctl: {exc}") from exc

        try:
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "unknown error").strip()
                raise CaptureError(
                    f"v4l2-ctl exited with status {result.returncode}: {detail}"
                )
            if not partial_path.exists():
                raise CaptureError("v4l2-ctl did not create an output file")

            actual_size = partial_path.stat().st_size
            if actual_size != FRAME_BYTES:
                raise CaptureError(
                    f"invalid Y16 frame size: expected {FRAME_BYTES}, got {actual_size}"
                )

            partial_path.replace(frame_path)
            captured_timestamp = captured_at.timestamp()
            os.utime(frame_path, (captured_timestamp, captured_timestamp))
            self.log(f"stored frame: {frame_path} ({FRAME_BYTES} bytes)")
            return frame_path
        except Exception:
            partial_path.unlink(missing_ok=True)
            raise

    def build_payload(self, frame_path: Path) -> dict[str, Any]:
        pixels = read_y16_frame(frame_path)
        raw_min = min(pixels)
        raw_max = max(pixels)
        raw_average = sum(pixels) / PIXEL_COUNT
        measured_at = datetime.fromtimestamp(
            frame_path.stat().st_mtime,
            tz=timezone.utc,
        )

        return {
            "device_id": self.device_id,
            "location_id": self.location_id,
            "sensor_type": "thermal",
            "timestamp": measured_at.isoformat(),
            "metrics": {
                "valid": True,
                "avg_temp": round(centikelvin_to_celsius(raw_average), 2),
                "min_temp": round(centikelvin_to_celsius(raw_min), 2),
                "max_temp": round(centikelvin_to_celsius(raw_max), 2),
                "width": WIDTH,
                "height": HEIGHT,
                "pixel_format": "Y16",
                "endianness": "little",
                "temperature_scale_k": 0.01,
                "temperature_offset_c": -273.15,
                "pixels": list(pixels),
            },
        }

    def upload(self, frame_path: Path) -> dict[str, Any]:
        payload = self.build_payload(frame_path)
        body = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "CDAS-Lepton35-Client/1.0",
            },
        )

        self.log(f"uploading frame to {self.endpoint}")
        try:
            with self._opener(request, timeout=self.request_timeout) as response:
                response_body = response.read()
                status_code = response.getcode()
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace").strip()
            except OSError:
                detail = ""
            suffix = f": {detail}" if detail else ""
            raise UploadError(f"server returned HTTP {exc.code}{suffix}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise UploadError(f"failed to reach {self.endpoint}: {exc}") from exc

        if not 200 <= status_code < 300:
            raise UploadError(f"server returned HTTP {status_code}")

        if not response_body:
            return {}
        try:
            decoded = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UploadError("server returned an invalid JSON response") from exc
        if not isinstance(decoded, dict):
            raise UploadError("server response must be a JSON object")
        return decoded

    def deliver(self, frame_path: Path) -> None:
        self.upload(frame_path)
        frame_path.unlink()
        self.log(f"uploaded and removed temporary frame: {frame_path.name}")

    def retry_pending(self) -> int:
        delivered = 0
        for frame_path in sorted(self.work_dir.glob("*.y16")):
            try:
                self.deliver(frame_path)
                delivered += 1
            except (UploadError, OSError, ValueError) as exc:
                self.log(f"pending upload failed; retained {frame_path.name}: {exc}")
                break
        return delivered

    def run_cycle(self) -> None:
        self.retry_pending()
        frame_path = self.capture_once()
        try:
            self.deliver(frame_path)
        except (UploadError, OSError, ValueError):
            # The valid frame remains in /tmp/rbp_client and is retried during
            # the next cycle or after a process restart.
            raise

    def run_forever(self) -> None:
        self.prepare()
        self.log(
            "started "
            f"device={self.dev_path} interval={self.interval:g}s "
            f"endpoint={self.endpoint}"
        )
        next_run = self._monotonic()

        while True:
            delay = next_run - self._monotonic()
            if delay > 0:
                self._sleep(delay)

            try:
                self.run_cycle()
            except (LeptonClientError, OSError, ValueError) as exc:
                self.log(f"cycle failed: {exc}")

            next_run += self.interval
            now = self._monotonic()
            if next_run <= now:
                next_run = now + self.interval

    def log(self, message: str) -> None:
        if self.verbose:
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            print(f"[{timestamp}] {message}", file=sys.stderr, flush=True)


def read_y16_frame(frame_path: Path) -> array[int]:
    try:
        frame_bytes = frame_path.read_bytes()
    except OSError as exc:
        raise CaptureError(f"failed to read {frame_path}: {exc}") from exc
    if len(frame_bytes) != FRAME_BYTES:
        raise CaptureError(
            f"invalid Y16 frame size: expected {FRAME_BYTES}, got {len(frame_bytes)}"
        )

    pixels = array("H")
    pixels.frombytes(frame_bytes)
    if sys.byteorder != "little":
        pixels.byteswap()
    if len(pixels) != PIXEL_COUNT:
        raise CaptureError(
            f"invalid Y16 pixel count: expected {PIXEL_COUNT}, got {len(pixels)}"
        )
    return pixels


def centikelvin_to_celsius(value: float) -> float:
    return value / 100.0 - 273.15


def normalize_server_endpoint(server_ip: str) -> str:
    if not isinstance(server_ip, str) or not server_ip.strip():
        raise ValueError("server_ip must be a non-empty address")

    candidate = server_ip.strip()
    if "://" not in candidate:
        candidate = "http://" + candidate

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("server_ip must be an HTTP(S) address or IP address")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("server_ip must not include user credentials")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("server_ip contains an invalid port") from exc

    if port is None:
        host = parsed.hostname
        assert host is not None
        host_part = f"[{host}]" if ":" in host else host
        netloc = f"{host_part}:{DEFAULT_SERVER_PORT}"
    else:
        netloc = parsed.netloc

    path = parsed.path.rstrip("/")
    if not path:
        path = DEFAULT_ENDPOINT_PATH
    elif not path.endswith(DEFAULT_ENDPOINT_PATH):
        path += DEFAULT_ENDPOINT_PATH

    return urlunparse((parsed.scheme, netloc, path, "", "", ""))


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture Lepton 3.5 Y16 frames from a PureThermal UVC device "
            "and send them to the CDAS server."
        )
    )
    parser.add_argument(
        "-i",
        "--ip",
        dest="server_ip",
        required=True,
        help="server IP, IP:port, or base HTTP URL",
    )
    parser.add_argument(
        "-d",
        "--dev",
        dest="dev_path",
        required=True,
        help="V4L2 device path, for example /dev/video0",
    )
    parser.add_argument(
        "-t",
        "--interval",
        type=float,
        required=True,
        help="capture interval in seconds",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable runtime logs",
    )
    return parser


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()

    try:
        client = LeptonSensorClient(
            args.server_ip,
            args.dev_path,
            args.interval,
            args.verbose,
        )
        client.run_forever()
    except KeyboardInterrupt:
        if args.verbose:
            print("Lepton sensor client stopped", file=sys.stderr)
        return 130
    except (LeptonClientError, ValueError, OSError) as exc:
        if args.verbose:
            print(f"Lepton sensor client error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
