from __future__ import annotations

import argparse
import queue
import threading
import time
from typing import Callable

from communication_thread import CommunicationWorker
from environment_config import (
    ConfigurationError,
    DEFAULT_ENVIRONMENT_PATH,
    EnvironmentConfig,
    load_environment,
)
from fusion_thread import FusionWorker
from lidar_sensor_thread import LidarSensorWorker
from model_adapter_thread import ModelAdapterWorker
from shared_runtime import (
    FusedSensorData,
    LidarScan,
    LOGGER,
    ManagedWorker,
    ResultMailbox,
    ThermalFrame,
    ThreadFailure,
    configure_logging,
    drain_queue,
)
from thermal_sensor_thread import ThermalSensorWorker


class SensorClientApplication:
    def __init__(
        self,
        environment: EnvironmentConfig,
        *,
        debug: bool,
        verbose: bool,
        video: bool = False,
    ) -> None:
        self.environment = environment
        self.debug = debug
        self.verbose = verbose
        self.video = video
        self.stop_event = threading.Event()
        self.failure_queue: queue.Queue[ThreadFailure] = queue.Queue()
        self.thermal_queue: queue.Queue[ThermalFrame] = queue.Queue(
            maxsize=environment.fusion.sensor_queue_size
        )
        self.lidar_queue: queue.Queue[LidarScan] = queue.Queue(
            maxsize=environment.fusion.sensor_queue_size
        )
        self.fused_queue: queue.Queue[FusedSensorData] = queue.Queue(
            maxsize=environment.fusion.fused_queue_size
        )
        self.mailbox = ResultMailbox()
        self.workers: dict[str, ManagedWorker] = {}
        self._restart_at: dict[str, float] = {}
        self._worker_factories = self._create_worker_factories()

    def _create_worker_factories(self) -> dict[str, Callable[[], ManagedWorker]]:
        common = {
            "environment": self.environment,
            "stop_event": self.stop_event,
            "failure_queue": self.failure_queue,
            "verbose": self.verbose,
        }
        return {
            "Communication": lambda: CommunicationWorker(mailbox=self.mailbox, **common),
            "ModelAdapter": lambda: ModelAdapterWorker(
                fused_queue=self.fused_queue,
                mailbox=self.mailbox,
                debug=self.debug,
                video=self.video,
                **common,
            ),
            "Fusion": lambda: FusionWorker(
                thermal_queue=self.thermal_queue,
                lidar_queue=self.lidar_queue,
                fused_queue=self.fused_queue,
                debug=self.debug,
                video=self.video,
                **common,
            ),
            "ThermalSensor": lambda: ThermalSensorWorker(
                output_queue=self.thermal_queue,
                video=self.video,
                **common,
            ),
            "LidarSensor": lambda: LidarSensorWorker(
                output_queue=self.lidar_queue,
                video=self.video,
                **common,
            ),
        }

    def _start_worker(self, name: str, *, restarting: bool = False) -> None:
        if self.stop_event.is_set():
            return
        try:
            worker = self._worker_factories[name]()
            # Track before start so shutdown can also clean up a partial startup.
            self.workers[name] = worker
            worker.start()
        except Exception as exc:
            self._schedule_restart(name, f"{type(exc).__name__}: {exc}")
            return
        if restarting:
            LOGGER.info("child thread %s restarted", name)

    def _schedule_restart(self, name: str, message: str) -> None:
        if self.stop_event.is_set() or name in self._restart_at:
            return
        delay = self.environment.runtime.worker_restart_delay_seconds
        self._restart_at[name] = time.monotonic() + delay
        LOGGER.error(
            "child thread %s stopped abnormally: %s; retrying in %.3f seconds",
            name,
            message,
            delay,
        )

    def _supervise_workers(self) -> None:
        while True:
            try:
                failure = self.failure_queue.get_nowait()
            except queue.Empty:
                break
            self._schedule_restart(failure.thread_name, failure.message)

        for name in self._worker_factories:
            if self.stop_event.is_set():
                return
            worker = self.workers.get(name)
            if name not in self._restart_at:
                if worker is not None and not worker.is_alive():
                    self._schedule_restart(name, "thread exited without a stop request")
                continue
            # A failure can be reported just before run() actually returns.
            # Never replace a worker until it has finished releasing resources.
            if worker is not None and worker.is_alive():
                continue
            if time.monotonic() < self._restart_at[name]:
                continue
            self._restart_at.pop(name)
            self._start_worker(name, restarting=True)

    def run(self) -> int:
        LOGGER.info("sensor client started")
        try:
            for name in self._worker_factories:
                if self.stop_event.is_set():
                    break
                self._start_worker(name)
            while not self.stop_event.is_set():
                self._supervise_workers()
                self.stop_event.wait(0.25)
        except KeyboardInterrupt:
            LOGGER.info("Ctrl+C received; stopping sensor client")
        finally:
            self.shutdown()
            while True:
                try:
                    failure = self.failure_queue.get_nowait()
                except queue.Empty:
                    break
                LOGGER.error(
                    "child thread %s stopped abnormally: %s",
                    failure.thread_name,
                    failure.message,
                )
            LOGGER.info("sensor client stopped")
        return 0

    def shutdown(self) -> None:
        self.stop_event.set()
        self.mailbox.close()
        join_timeout = max(
            self.environment.thermal.capture_timeout_seconds,
            self.environment.lidar.timeout_ms / 1000,
            self.environment.server.request_timeout_seconds,
        ) + 3.0
        for worker in self.workers.values():
            if worker.ident is None:
                continue
            worker.join(timeout=join_timeout)
            if worker.is_alive():
                LOGGER.error("thread did not stop before timeout: %s", worker.name)
        drain_queue(self.thermal_queue)
        drain_queue(self.lidar_queue)
        drain_queue(self.fused_queue)
        self._restart_at.clear()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the CDAS Raspberry Pi sensor inference client."
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "save image-mode debug PNGs or show the video-mode GUI; use "
            "random inference when the adapter module is unavailable"
        ),
    )
    parser.add_argument(
        "--video",
        "-v",
        action="store_true",
        help="use continuous video inference instead of image inference",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="enable successful sensor, fusion, inference, and heartbeat logs",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    configure_logging()
    try:
        environment = load_environment(DEFAULT_ENVIRONMENT_PATH)
    except ConfigurationError as exc:
        LOGGER.error(str(exc))
        return 1
    application = SensorClientApplication(
        environment,
        debug=args.debug,
        verbose=args.verbose,
        video=args.video,
    )
    return application.run()


if __name__ == "__main__":
    raise SystemExit(main())
