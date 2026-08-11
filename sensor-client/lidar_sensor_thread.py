from __future__ import annotations

import json
import math
import queue
import selectors
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from environment_config import EnvironmentConfig
from shared_runtime import (
    LidarScan,
    ManagedWorker,
    SensorError,
    ThreadFailure,
    put_with_stop,
    stop_process,
)


class LidarSensorWorker(ManagedWorker):
    def __init__(
        self,
        *,
        environment: EnvironmentConfig,
        output_queue: queue.Queue[LidarScan],
        stop_event: threading.Event,
        failure_queue: queue.Queue[ThreadFailure],
        verbose: bool,
        process_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
        validate_hardware: bool = True,
    ) -> None:
        super().__init__(
            name="LidarSensor",
            stop_event=stop_event,
            failure_queue=failure_queue,
            verbose=verbose,
        )
        self.config = environment.lidar
        self.output_queue = output_queue
        self.process_factory = process_factory
        self.validate_hardware = validate_hardware

    def run_worker(self) -> None:
        if self.validate_hardware:
            if not self.config.bridge_path.is_file():
                raise SensorError(
                    f"RPLIDAR bridge does not exist: {self.config.bridge_path}"
                )
            if not Path(self.config.device_path).exists():
                raise SensorError(
                    f"LiDAR device does not exist: {self.config.device_path}"
                )

        command = [
            str(self.config.bridge_path),
            "--port",
            self.config.device_path,
            "--baud",
            str(self.config.baud_rate),
            "--scan-mode",
            self.config.scan_mode,
            "--timeout-ms",
            str(self.config.timeout_ms),
        ]
        try:
            process = self.process_factory(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise SensorError(f"failed to start RPLIDAR bridge: {exc}") from exc

        try:
            self._consume_bridge(process)
        finally:
            stop_process(process)

    def _consume_bridge(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None or process.stderr is None:
            raise SensorError("RPLIDAR bridge pipes were not created")

        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        try:
            while not self.stop_event.is_set():
                for key, _ in selector.select(timeout=0.5):
                    line = key.fileobj.readline()
                    if line == "":
                        try:
                            selector.unregister(key.fileobj)
                        except KeyError:
                            pass
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    if key.data == "stderr":
                        self.verbose_info("RPLIDAR bridge: %s", line)
                    else:
                        self._handle_bridge_record(line)

                return_code = process.poll()
                if return_code is not None:
                    if self.stop_event.is_set():
                        return
                    raise SensorError(
                        f"RPLIDAR bridge exited with status {return_code}"
                    )
        finally:
            selector.close()

    def _handle_bridge_record(self, line: str) -> None:
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SensorError("RPLIDAR bridge emitted invalid JSON") from exc
        if not isinstance(record, dict):
            raise SensorError("RPLIDAR bridge record must be an object")

        record_type = record.get("type")
        if record_type == "ready":
            self.verbose_info("communicating with SLAMTEC C1")
            return
        if record_type != "scan":
            raise SensorError(f"unsupported RPLIDAR record type: {record_type!r}")

        sequence = record.get("sequence")
        timestamp_ms = record.get("timestamp_unix_ms")
        raw_points = record.get("points")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise SensorError("RPLIDAR sequence must be a non-negative integer")
        if (
            isinstance(timestamp_ms, bool)
            or not isinstance(timestamp_ms, (int, float))
            or not math.isfinite(float(timestamp_ms))
        ):
            raise SensorError("RPLIDAR timestamp_unix_ms must be finite")
        if not isinstance(raw_points, list):
            raise SensorError("RPLIDAR points must be an array")

        points: list[tuple[float, float, int]] = []
        for point in raw_points:
            if not isinstance(point, list) or len(point) != 3:
                raise SensorError("RPLIDAR point must contain angle, distance, quality")
            angle, distance, quality = point
            if (
                isinstance(angle, bool)
                or not isinstance(angle, (int, float))
                or isinstance(distance, bool)
                or not isinstance(distance, (int, float))
                or isinstance(quality, bool)
                or not isinstance(quality, int)
                or not math.isfinite(float(angle))
                or not math.isfinite(float(distance))
                or distance <= 0
                or quality < 0
            ):
                raise SensorError("RPLIDAR point contains invalid values")
            points.append((float(angle), float(distance), quality))

        try:
            captured_at = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        except (OSError, OverflowError, ValueError) as exc:
            raise SensorError("RPLIDAR timestamp_unix_ms is out of range") from exc
        scan = LidarScan(
            captured_at=captured_at,
            sequence=sequence,
            points=tuple(points),
        )
        if put_with_stop(self.output_queue, scan, self.stop_event):
            self.verbose_info("received SLAMTEC C1 scan sequence=%d", sequence)
