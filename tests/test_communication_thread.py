from __future__ import annotations

import json
import queue
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request


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
)
from communication_thread import CommunicationWorker  # noqa: E402
from shared_runtime import (  # noqa: E402
    CommunicationError,
    InferenceResult,
    ResultMailbox,
)


def make_environment(root: Path) -> EnvironmentConfig:
    return EnvironmentConfig(
        node=NodeConfig("pi-001", "gate-1"),
        thermal=ThermalConfig("/dev/video0", 1.0, 5.0),
        lidar=LidarConfig(root / "bridge", "/dev/ttyUSB0", 460800, "Standard", 2000),
        fusion=FusionConfig(1.0, 10, 8, 2, root / "debug", 640, 12.0),
        model=ModelConfig(None, None),
        server=ServerConfig("http://127.0.0.1:8000", 1.0, 1.0, 0.5, 3),
    )


class CommunicationWorkerTest(unittest.TestCase):
    def test_sends_only_inference_result_and_performs_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment = make_environment(Path(temporary_directory))
            requests: list[Request] = []

            def opener(request: Request, **_: object) -> _FakeResponse:
                requests.append(request)
                return _FakeResponse(201 if request.data is not None else 200)

            worker = CommunicationWorker(
                environment=environment,
                mailbox=ResultMailbox(),
                stop_event=threading.Event(),
                failure_queue=queue.Queue(),
                verbose=False,
                opener=opener,
            )
            now = datetime.now(timezone.utc)
            worker._send_result_until_complete(
                InferenceResult("pi-001", "gate-1", now, 8, 0.75)
            )
            worker._heartbeat()

            payload = json.loads(requests[0].data.decode("utf-8"))
            self.assertEqual(
                set(payload),
                {
                    "node_id",
                    "location_id",
                    "timestamp",
                    "people_count",
                    "confidence",
                },
            )
            self.assertEqual(payload["people_count"], 8)
            self.assertTrue(requests[0].full_url.endswith("/api/inference-results"))
            self.assertEqual(requests[1].get_method(), "GET")
            self.assertTrue(requests[1].full_url.endswith("/health"))

    def test_repeated_communication_failures_become_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment = make_environment(Path(temporary_directory))

            def offline(*_: object, **__: object) -> None:
                raise URLError("offline")

            worker = CommunicationWorker(
                environment=environment,
                mailbox=ResultMailbox(),
                stop_event=threading.Event(),
                failure_queue=queue.Queue(),
                verbose=False,
                opener=offline,
            )
            request = Request("http://127.0.0.1:8000/health")
            self.assertFalse(worker._perform_request(request, "heartbeat"))
            self.assertFalse(worker._perform_request(request, "heartbeat"))
            with self.assertRaises(CommunicationError):
                worker._perform_request(request, "heartbeat")


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status_code

    def read(self) -> bytes:
        return b"{}"


if __name__ == "__main__":
    unittest.main()
