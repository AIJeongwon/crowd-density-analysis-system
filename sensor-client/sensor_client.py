from __future__ import annotations

import argparse
import queue
import threading

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
    ) -> None:
        self.environment = environment
        self.debug = debug
        self.verbose = verbose
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
        self.workers: list[ManagedWorker] = self._create_workers()

    def _create_workers(self) -> list[ManagedWorker]:
        common = {
            "environment": self.environment,
            "stop_event": self.stop_event,
            "failure_queue": self.failure_queue,
            "verbose": self.verbose,
        }
        return [
            CommunicationWorker(mailbox=self.mailbox, **common),
            ModelAdapterWorker(
                fused_queue=self.fused_queue,
                mailbox=self.mailbox,
                debug=self.debug,
                **common,
            ),
            FusionWorker(
                thermal_queue=self.thermal_queue,
                lidar_queue=self.lidar_queue,
                fused_queue=self.fused_queue,
                debug=self.debug,
                **common,
            ),
            ThermalSensorWorker(output_queue=self.thermal_queue, **common),
            LidarSensorWorker(output_queue=self.lidar_queue, **common),
        ]

    def run(self) -> int:
        LOGGER.info("sensor client started")
        exit_code = 0
        for worker in self.workers:
            worker.start()
        try:
            while True:
                try:
                    failure = self.failure_queue.get(timeout=0.25)
                except queue.Empty:
                    if self.stop_event.is_set():
                        break
                    continue
                LOGGER.error(
                    "child thread %s stopped abnormally: %s",
                    failure.thread_name,
                    failure.message,
                )
                exit_code = 1
                self.stop_event.set()
                break
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
                exit_code = 1
            LOGGER.info("sensor client stopped")
        return exit_code

    def shutdown(self) -> None:
        self.stop_event.set()
        self.mailbox.close()
        join_timeout = max(
            self.environment.thermal.capture_timeout_seconds,
            self.environment.lidar.timeout_ms / 1000,
        ) + 3.0
        for worker in self.workers:
            worker.join(timeout=join_timeout)
            if worker.is_alive():
                LOGGER.error("thread did not stop before timeout: %s", worker.name)
        drain_queue(self.thermal_queue)
        drain_queue(self.lidar_queue)
        drain_queue(self.fused_queue)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the CDAS Raspberry Pi sensor inference client."
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="save fused sensor debug images and allow a missing model",
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
    )
    return application.run()


if __name__ == "__main__":
    raise SystemExit(main())
