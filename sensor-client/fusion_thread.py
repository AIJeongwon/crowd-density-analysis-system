from __future__ import annotations

import queue
import threading

from debug_images import save_lidar_png, save_thermal_png
from environment_config import EnvironmentConfig
from shared_runtime import (
    FusedSensorData,
    LidarScan,
    ManagedWorker,
    ThermalFrame,
    ThreadFailure,
    drain_queue,
    put_with_stop,
)


class FusionWorker(ManagedWorker):
    def __init__(
        self,
        *,
        environment: EnvironmentConfig,
        thermal_queue: queue.Queue[ThermalFrame],
        lidar_queue: queue.Queue[LidarScan],
        fused_queue: queue.Queue[FusedSensorData],
        stop_event: threading.Event,
        failure_queue: queue.Queue[ThreadFailure],
        debug: bool,
        verbose: bool,
    ) -> None:
        super().__init__(
            name="Fusion",
            stop_event=stop_event,
            failure_queue=failure_queue,
            verbose=verbose,
        )
        self.config = environment.fusion
        self.thermal_queue = thermal_queue
        self.lidar_queue = lidar_queue
        self.fused_queue = fused_queue
        self.debug = debug
        self.check_count = 0

    def run_worker(self) -> None:
        while not self.stop_event.is_set():
            self.check_once()
            self.stop_event.wait(self.config.poll_interval_seconds)

    def check_once(self) -> FusedSensorData | None:
        self.check_count += 1
        if self.check_count % self.config.flush_every_checks == 0:
            thermal_count = drain_queue(self.thermal_queue)
            lidar_count = drain_queue(self.lidar_queue)
            self.verbose_info(
                "cleared stale sensor queues thermal=%d lidar=%d",
                thermal_count,
                lidar_count,
            )
            return None

        if self.thermal_queue.empty() or self.lidar_queue.empty():
            return None
        try:
            thermal = self.thermal_queue.get_nowait()
            lidar = self.lidar_queue.get_nowait()
        except queue.Empty:
            return None

        fused = FusedSensorData(
            fused_at=max(thermal.captured_at, lidar.captured_at),
            thermal=thermal,
            lidar=lidar,
        )
        if self.debug:
            self._save_debug_images(fused)
        if not put_with_stop(self.fused_queue, fused, self.stop_event):
            return None
        self.verbose_info(
            "fused thermal frame and LiDAR scan sequence=%d and queued result",
            lidar.sequence,
        )
        return fused

    def _save_debug_images(self, fused: FusedSensorData) -> None:
        timestamp = fused.fused_at.strftime("%Y%m%dT%H%M%S_%fZ")
        save_thermal_png(
            fused.thermal.pixels,
            fused.thermal.width,
            fused.thermal.height,
            self.config.debug_dir / f"{timestamp}_thermal.png",
        )
        save_lidar_png(
            fused.lidar.points,
            self.config.debug_dir / f"{timestamp}_lidar.png",
            width=self.config.lidar_image_size,
            height=self.config.lidar_image_size,
            max_distance_m=self.config.lidar_max_distance_m,
        )
