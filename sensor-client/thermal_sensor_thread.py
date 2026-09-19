from __future__ import annotations

import os
import queue
import selectors
import shutil
import subprocess
import tempfile
import threading
import time
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
    put_latest_with_stop,
    put_with_stop,
    python_is_little_endian,
    stop_process,
)


VIDEO_READ_CHUNK_BYTES = THERMAL_FRAME_BYTES * 2
STDERR_TAIL_BYTES = 4096


class ThermalSensorWorker(ManagedWorker):
    def __init__(
        self,
        *,
        environment: EnvironmentConfig,
        output_queue: queue.Queue[ThermalFrame],
        stop_event: threading.Event,
        failure_queue: queue.Queue[ThreadFailure],
        verbose: bool,
        video: bool = False,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
        process_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
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
        self.video = video
        self.runner = runner
        self.process_factory = process_factory
        self.validate_hardware = validate_hardware

    def run_worker(self) -> None:
        if self.validate_hardware:
            if shutil.which("v4l2-ctl") is None:
                raise SensorError("v4l2-ctl was not found in PATH")
            if not Path(self.config.device_path).exists():
                raise SensorError(
                    f"thermal device does not exist: {self.config.device_path}"
                )

        if self.video:
            self._run_video_stream()
            return

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

        return self._decode_frame(frame_bytes, captured_at)

    def build_video_command(self) -> list[str]:
        return [
            "v4l2-ctl",
            f"--device={self.config.device_path}",
            (
                "--set-fmt-video="
                f"width={THERMAL_WIDTH},height={THERMAL_HEIGHT},"
                f"pixelformat={Y16_FOURCC}"
            ),
            "--stream-mmap=4",
            "--stream-to=-",
        ]

    def _run_video_stream(self) -> None:
        command = self.build_video_command()
        self.verbose_info(
            "streaming Lepton 3.5 video on %s",
            self.config.device_path,
        )
        try:
            process = self.process_factory(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except OSError as exc:
            raise SensorError(f"failed to start Lepton video stream: {exc}") from exc

        try:
            self._consume_video_stream(process)
        finally:
            stop_process(process)

    def _consume_video_stream(self, process: subprocess.Popen[bytes]) -> None:
        if process.stdout is None or process.stderr is None:
            raise SensorError("Lepton video stream pipes were not created")

        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        pending = bytearray()
        stderr_tail = bytearray()
        last_frame_at = time.monotonic()
        try:
            while not self.stop_event.is_set():
                for key, _ in selector.select(timeout=0.25):
                    try:
                        chunk = os.read(key.fileobj.fileno(), VIDEO_READ_CHUNK_BYTES)
                    except OSError as exc:
                        raise SensorError(
                            f"failed to read Lepton video stream: {exc}"
                        ) from exc
                    if not chunk:
                        try:
                            selector.unregister(key.fileobj)
                        except KeyError:
                            pass
                        continue
                    if key.data == "stderr":
                        stderr_tail.extend(chunk)
                        if len(stderr_tail) > STDERR_TAIL_BYTES:
                            del stderr_tail[:-STDERR_TAIL_BYTES]
                        continue

                    pending.extend(chunk)
                    while len(pending) >= THERMAL_FRAME_BYTES:
                        frame_bytes = bytes(pending[:THERMAL_FRAME_BYTES])
                        del pending[:THERMAL_FRAME_BYTES]
                        frame = self._decode_frame(
                            frame_bytes,
                            datetime.now(timezone.utc),
                        )
                        if not put_latest_with_stop(
                            self.output_queue,
                            frame,
                            self.stop_event,
                        ):
                            return
                        last_frame_at = time.monotonic()

                return_code = process.poll()
                if return_code is not None:
                    if self.stop_event.is_set():
                        return
                    detail = decode_process_output(bytes(stderr_tail))
                    raise SensorError(
                        "Lepton video stream exited with status "
                        f"{return_code}: {detail}"
                    )
                if (
                    time.monotonic() - last_frame_at
                    > self.config.capture_timeout_seconds
                ):
                    raise SensorError("Lepton video stream timed out")
        finally:
            selector.close()

    def _decode_frame(
        self,
        frame_bytes: bytes,
        captured_at: datetime,
    ) -> ThermalFrame:
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
        rotated_pixels = _rotate_pixels_clockwise(
            pixels,
            width=THERMAL_WIDTH,
            height=THERMAL_HEIGHT,
        )
        return ThermalFrame(
            captured_at=captured_at,
            pixels=rotated_pixels,
            width=THERMAL_HEIGHT,
            height=THERMAL_WIDTH,
        )


def _rotate_pixels_clockwise(
    pixels: array[int],
    *,
    width: int,
    height: int,
) -> tuple[int, ...]:
    """Rotate a row-major scalar image clockwise by 90 degrees."""

    return tuple(
        pixels[row * width + column]
        for column in range(width)
        for row in range(height - 1, -1, -1)
    )
