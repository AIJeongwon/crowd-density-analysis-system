from __future__ import annotations

import os
import queue
import shutil
import subprocess
import tempfile
import threading
from array import array
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from environment_config import EnvironmentConfig
from shared_runtime import (
    ManagedWorker,
    SensorError,
    THERMAL_FRAME_BYTES,
    THERMAL_HEIGHT,
    THERMAL_PIXEL_COUNT,
    THERMAL_WIDTH,
    ThermalFrame,
    ThreadFailure,
    Y16_FOURCC,
    decode_process_output,
    put_with_stop,
    python_is_little_endian,
)


class ThermalSensorWorker(ManagedWorker):
    def __init__(
        self,
        *,
        environment: EnvironmentConfig,
        output_queue: queue.Queue[ThermalFrame],
        stop_event: threading.Event,
        failure_queue: queue.Queue[ThreadFailure],
        verbose: bool,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
        validate_hardware: bool = True,
    ) -> None:
        super().__init__(
            name="ThermalSensor",
            stop_event=stop_event,
            failure_queue=failure_queue,
            verbose=verbose,
        )
        self.config = environment.thermal
        self.output_queue = output_queue
        self.runner = runner
        self.validate_hardware = validate_hardware

    def run_worker(self) -> None:
        if self.validate_hardware:
            if shutil.which("v4l2-ctl") is None:
                raise SensorError("v4l2-ctl was not found in PATH")
            if not Path(self.config.device_path).exists():
                raise SensorError(
                    f"thermal device does not exist: {self.config.device_path}"
                )

        while not self.stop_event.is_set():
            self.verbose_info(
                "communicating with Lepton 3.5 on %s",
                self.config.device_path,
            )
            frame = self.capture_once()
            if not put_with_stop(self.output_queue, frame, self.stop_event):
                return
            self.stop_event.wait(self.config.capture_interval_seconds)

    def capture_once(self) -> ThermalFrame:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="cdas_lepton_",
            suffix=".y16.part",
            dir="/tmp",
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        captured_at = datetime.now(timezone.utc)
        command = [
            "v4l2-ctl",
            f"--device={self.config.device_path}",
            (
                "--set-fmt-video="
                f"width={THERMAL_WIDTH},height={THERMAL_HEIGHT},"
                f"pixelformat={Y16_FOURCC}"
            ),
            "--stream-mmap=4",
            "--stream-count=1",
            f"--stream-to={temporary_path}",
        ]
        try:
            result = self.runner(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.config.capture_timeout_seconds,
                check=False,
            )
            if result.returncode != 0:
                detail = decode_process_output(result.stderr)
                raise SensorError(
                    f"v4l2-ctl exited with status {result.returncode}: {detail}"
                )
            frame_bytes = temporary_path.read_bytes()
        except subprocess.TimeoutExpired as exc:
            raise SensorError("Lepton capture timed out") from exc
        except OSError as exc:
            raise SensorError(f"failed to capture Lepton frame: {exc}") from exc
        finally:
            temporary_path.unlink(missing_ok=True)

        if len(frame_bytes) != THERMAL_FRAME_BYTES:
            raise SensorError(
                f"invalid Y16 frame size: expected {THERMAL_FRAME_BYTES}, "
                f"got {len(frame_bytes)}"
            )
        pixels = array("H")
        pixels.frombytes(frame_bytes)
        if not python_is_little_endian():
            pixels.byteswap()
        if len(pixels) != THERMAL_PIXEL_COUNT:
            raise SensorError("Y16 frame contains an invalid pixel count")
        return ThermalFrame(captured_at=captured_at, pixels=tuple(pixels))
