from __future__ import annotations

import io
import json
import logging
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path


SENSOR_CLIENT_DIR = Path(__file__).resolve().parents[1] / "sensor-client"
if str(SENSOR_CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(SENSOR_CLIENT_DIR))

from environment_config import (  # noqa: E402
    EnvironmentConfig,
    FusionConfig,
    LidarConfig,
    ModelConfig,
    NodeConfig,
    ServerConfig,
    ThermalConfig,
    load_environment,
)
from fusion_thread import FusionWorker  # noqa: E402
from lidar_sensor_thread import LidarSensorWorker  # noqa: E402
from model_adapter_thread import (  # noqa: E402
    ModelAdapterWorker,
    parse_inference_output,
)
from shared_runtime import (  # noqa: E402
    FusedSensorData,
    InferenceResult,
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    LidarScan,
    ModelError,
    ResultMailbox,
    ThermalFrame,
)
from sensor_client import (  # noqa: E402
    DEFAULT_ENVIRONMENT_PATH,
    build_argument_parser,
)
from thermal_sensor_thread import ThermalSensorWorker  # noqa: E402


def make_environment(root: Path) -> EnvironmentConfig:
    return EnvironmentConfig(
        node=NodeConfig(node_id="pi-001", location_id="gate-1"),
        thermal=ThermalConfig(
            device_path="/dev/video-test",
            capture_interval_seconds=0.01,
            capture_timeout_seconds=1.0,
        ),
        lidar=LidarConfig(
            bridge_path=root / "rplidar_bridge",
            device_path="/dev/tty-test",
            baud_rate=460800,
            scan_mode="Standard",
            timeout_ms=100,
        ),
        fusion=FusionConfig(
            poll_interval_seconds=0.01,
            flush_every_checks=10,
            sensor_queue_size=8,
            fused_queue_size=2,
            debug_dir=root / "debug",
            lidar_image_size=64,
            lidar_max_distance_m=12.0,
        ),
        model=ModelConfig(adapter_module=None, model_path=None),
        server=ServerConfig(
            base_url="http://127.0.0.1:8000",
            request_timeout_seconds=1.0,
            heartbeat_interval_seconds=1.0,
            heartbeat_warning_seconds=0.5,
            max_consecutive_failures=3,
        ),
    )


class SensorConfigurationTest(unittest.TestCase):
    def test_log_formatter_uses_two_digit_year_and_milliseconds(self) -> None:
        record = logging.LogRecord(
            "cdas.test",
            logging.INFO,
            __file__,
            1,
            "message",
            (),
            None,
        )
        record.msecs = 678.0
        record.threadName = "TestThread"

        line = logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT).format(record)

        self.assertRegex(
            line,
            r"^\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.678 ",
        )
        self.assertTrue(line.endswith("INFO [TestThread] message"))

    def test_loads_environment_and_resolves_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            payload = {
                "node": {"node_id": "pi-001", "location_id": "gate-1"},
                "thermal": {
                    "device_path": "/dev/video0",
                    "capture_interval_seconds": 1,
                    "capture_timeout_seconds": 10,
                },
                "lidar": {
                    "bridge_path": "bin/bridge",
                    "device_path": "/dev/ttyUSB0",
                    "baud_rate": 460800,
                    "scan_mode": "Standard",
                    "timeout_ms": 2000,
                },
                "fusion": {
                    "poll_interval_seconds": 1,
                    "flush_every_checks": 10,
                    "sensor_queue_size": 8,
                    "fused_queue_size": 2,
                    "debug_dir": "/tmp/cdas",
                    "lidar_image_size": 640,
                    "lidar_max_distance_m": 12,
                },
                "model": {"adapter_module": None, "model_path": None},
                "server": {
                    "base_url": "http://127.0.0.1:8000/",
                    "request_timeout_seconds": 5,
                    "heartbeat_interval_seconds": 10,
                    "heartbeat_warning_seconds": 1,
                    "max_consecutive_failures": 3,
                },
            }
            path = root / "environment.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            environment = load_environment(path)

            self.assertEqual(environment.node.location_id, "gate-1")
            self.assertEqual(environment.lidar.bridge_path, (root / "bin/bridge").resolve())
            self.assertEqual(environment.fusion.flush_every_checks, 10)
            self.assertEqual(environment.server.base_url, "http://127.0.0.1:8000")

    def test_default_environment_path_is_in_sensor_client_directory(self) -> None:
        self.assertEqual(
            DEFAULT_ENVIRONMENT_PATH,
            SENSOR_CLIENT_DIR / "environment.json",
        )

    def test_cli_accepts_only_debug_and_verbose_flags(self) -> None:
        parser = build_argument_parser()
        args = parser.parse_args(["--debug", "--verbose"])
        self.assertTrue(args.debug)
        self.assertTrue(args.verbose)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["--ip", "127.0.0.1"])


class FusionWorkerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.environment = make_environment(self.root)
        self.stop_event = threading.Event()
        self.failure_queue: queue.Queue = queue.Queue()
        self.thermal_queue: queue.Queue[ThermalFrame] = queue.Queue()
        self.lidar_queue: queue.Queue[LidarScan] = queue.Queue()
        self.fused_queue: queue.Queue[FusedSensorData] = queue.Queue()
        self.worker = FusionWorker(
            environment=self.environment,
            thermal_queue=self.thermal_queue,
            lidar_queue=self.lidar_queue,
            fused_queue=self.fused_queue,
            stop_event=self.stop_event,
            failure_queue=self.failure_queue,
            debug=False,
            verbose=False,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def add_pair(self, sequence: int = 1) -> None:
        now = datetime.now(timezone.utc)
        self.thermal_queue.put(ThermalFrame(now, (30000,) * 4, width=2, height=2))
        self.lidar_queue.put(LidarScan(now, sequence, ((0.0, 1000.0, 10),)))

    def test_fuses_one_item_from_each_sensor_queue(self) -> None:
        self.add_pair(sequence=7)
        fused = self.worker.check_once()
        self.assertIsNotNone(fused)
        self.assertEqual(self.fused_queue.get_nowait().lidar.sequence, 7)
        self.assertTrue(self.thermal_queue.empty())
        self.assertTrue(self.lidar_queue.empty())

    def test_tenth_check_clears_both_sensor_queues_without_fusing(self) -> None:
        self.add_pair()
        self.worker.check_count = 9
        fused = self.worker.check_once()
        self.assertIsNone(fused)
        self.assertTrue(self.thermal_queue.empty())
        self.assertTrue(self.lidar_queue.empty())
        self.assertTrue(self.fused_queue.empty())


class AdapterAndMailboxTest(unittest.TestCase):
    def test_debug_mode_enables_random_fallback_without_adapter_module(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment = make_environment(Path(temporary_directory))
            worker = ModelAdapterWorker(
                environment=environment,
                fused_queue=queue.Queue(),
                mailbox=ResultMailbox(),
                stop_event=threading.Event(),
                failure_queue=queue.Queue(),
                debug=True,
                verbose=False,
            )
            self.assertIsNone(worker._load_adapter())
            self.assertTrue(worker._random_fallback_enabled)

    def test_debug_mode_enables_random_fallback_for_missing_adapter_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment = replace(
                make_environment(root),
                model=ModelConfig(
                    adapter_module=root / "missing_adapter.py",
                    model_path=root / "model.bin",
                ),
            )
            worker = ModelAdapterWorker(
                environment=environment,
                fused_queue=queue.Queue(),
                mailbox=ResultMailbox(),
                stop_event=threading.Event(),
                failure_queue=queue.Queue(),
                debug=True,
                verbose=False,
            )

            self.assertIsNone(worker._load_adapter())
            self.assertTrue(worker._random_fallback_enabled)

    def test_debug_mode_skips_when_only_model_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            adapter_path = root / "adapter.py"
            adapter_path.write_text("class ModelAdapter: pass\n", encoding="utf-8")
            environment = replace(
                make_environment(root),
                model=ModelConfig(
                    adapter_module=adapter_path,
                    model_path=root / "missing_model.bin",
                ),
            )
            worker = ModelAdapterWorker(
                environment=environment,
                fused_queue=queue.Queue(),
                mailbox=ResultMailbox(),
                stop_event=threading.Event(),
                failure_queue=queue.Queue(),
                debug=True,
                verbose=False,
            )

            self.assertIsNone(worker._load_adapter())
            self.assertFalse(worker._random_fallback_enabled)

    def test_debug_mode_does_not_hide_adapter_import_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            adapter_path = root / "adapter.py"
            adapter_path.write_text(
                "raise RuntimeError('broken import')\n",
                encoding="utf-8",
            )
            model_path = root / "model.bin"
            model_path.write_bytes(b"model")
            environment = replace(
                make_environment(root),
                model=ModelConfig(
                    adapter_module=adapter_path,
                    model_path=model_path,
                ),
            )
            worker = ModelAdapterWorker(
                environment=environment,
                fused_queue=queue.Queue(),
                mailbox=ResultMailbox(),
                stop_event=threading.Event(),
                failure_queue=queue.Queue(),
                debug=True,
                verbose=False,
            )

            with self.assertRaisesRegex(ModelError, "failed to import"):
                worker._load_adapter()
            self.assertFalse(worker._random_fallback_enabled)

    def test_debug_random_fallback_publishes_result_and_logs_when_verbose(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment = make_environment(Path(temporary_directory))
            now = datetime.now(timezone.utc)
            fused_queue: queue.Queue[FusedSensorData] = queue.Queue()
            fused_queue.put(
                FusedSensorData(
                    now,
                    ThermalFrame(now, (30000,), width=1, height=1),
                    LidarScan(now, 1, ((0.0, 1000.0, 10),)),
                )
            )
            mailbox = ResultMailbox()
            stop_event = threading.Event()
            failure_queue: queue.Queue = queue.Queue()
            worker = ModelAdapterWorker(
                environment=environment,
                fused_queue=fused_queue,
                mailbox=mailbox,
                stop_event=stop_event,
                failure_queue=failure_queue,
                debug=True,
                verbose=True,
                random_people_count=lambda: 17,
            )

            with self.assertLogs("cdas.sensor_client", level="INFO") as logs:
                worker.start()
                try:
                    result = mailbox.take(1.0, stop_event)
                finally:
                    stop_event.set()
                    mailbox.close()
                    worker.join(timeout=1.0)

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result.people_count, 17)
            self.assertEqual(result.confidence, 0.0)
            self.assertEqual(result.node_id, "pi-001")
            self.assertEqual(result.location_id, "gate-1")
            self.assertTrue(failure_queue.empty())
            self.assertFalse(worker.is_alive())
            self.assertTrue(
                any(
                    "random inference queued for server people_count=17"
                    in message
                    for message in logs.output
                )
            )

    def test_normal_mode_rejects_missing_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment = make_environment(Path(temporary_directory))
            worker = ModelAdapterWorker(
                environment=environment,
                fused_queue=queue.Queue(),
                mailbox=ResultMailbox(),
                stop_event=threading.Event(),
                failure_queue=queue.Queue(),
                debug=False,
                verbose=False,
            )
            with self.assertRaises(ModelError):
                worker._load_adapter()

    def test_model_output_becomes_inference_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment = make_environment(Path(temporary_directory))
            now = datetime.now(timezone.utc)
            fused = FusedSensorData(
                now,
                ThermalFrame(now, (30000,), width=1, height=1),
                LidarScan(now, 1, ((0.0, 1000.0, 10),)),
            )
            result = parse_inference_output(
                {"people_count": 12, "confidence": 0.9},
                fused,
                environment,
            )
            self.assertEqual(result.people_count, 12)
            self.assertEqual(result.location_id, "gate-1")

    def test_mailbox_hands_off_one_result_without_queueing(self) -> None:
        mailbox = ResultMailbox()
        stop_event = threading.Event()
        now = datetime.now(timezone.utc)
        result = InferenceResult("pi-001", "gate-1", now, 3, 0.8)
        self.assertTrue(mailbox.publish(result, stop_event))
        self.assertEqual(mailbox.take(0.1, stop_event), result)
        self.assertIsNone(mailbox.take(0.01, stop_event))


class SensorParsingTest(unittest.TestCase):
    def test_thermal_capture_reads_little_endian_y16(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment = make_environment(Path(temporary_directory))

            def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
                output = next(value for value in command if value.startswith("--stream-to="))
                Path(output.split("=", 1)[1]).write_bytes(
                    (30000).to_bytes(2, "little") * (160 * 120)
                )
                return subprocess.CompletedProcess(command, 0, b"", b"")

            worker = ThermalSensorWorker(
                environment=environment,
                output_queue=queue.Queue(),
                stop_event=threading.Event(),
                failure_queue=queue.Queue(),
                verbose=False,
                runner=runner,
                validate_hardware=False,
            )
            frame = worker.capture_once()
            self.assertEqual(len(frame.pixels), 160 * 120)
            self.assertEqual(frame.pixels[0], 30000)

    def test_lidar_bridge_record_is_parsed_into_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment = make_environment(Path(temporary_directory))
            output_queue: queue.Queue[LidarScan] = queue.Queue()
            worker = LidarSensorWorker(
                environment=environment,
                output_queue=output_queue,
                stop_event=threading.Event(),
                failure_queue=queue.Queue(),
                verbose=False,
                validate_hardware=False,
            )
            worker._handle_bridge_record(
                json.dumps(
                    {
                        "type": "scan",
                        "sequence": 4,
                        "timestamp_unix_ms": 1786089600000,
                        "points": [[0.0, 1000.0, 15], [90.0, 2000.0, 20]],
                    }
                )
            )
            scan = output_queue.get_nowait()
            self.assertEqual(scan.sequence, 4)
            self.assertEqual(len(scan.points), 2)


if __name__ == "__main__":
    unittest.main()
