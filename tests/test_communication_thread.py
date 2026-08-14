from __future__ import annotations

import json
import queue
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from http.client import RemoteDisconnected
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
)
from communication_thread import CommunicationWorker  # noqa: E402
from shared_runtime import CommunicationError, InferenceResult, ResultMailbox  # noqa: E402


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
    def make_worker(
        self,
        root: Path,
        factory: "_ConnectionFactory",
        stop_event: threading.Event | None = None,
    ) -> CommunicationWorker:
        return CommunicationWorker(
            environment=make_environment(root),
            mailbox=ResultMailbox(),
            stop_event=stop_event or threading.Event(),
            failure_queue=queue.Queue(),
            verbose=False,
            connection_factory=factory,
        )

    def test_reuses_one_connection_for_inference_and_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            connection = _FakeConnection([_FakeResponse(201), _FakeResponse(200)])
            factory = _ConnectionFactory([connection])
            worker = self.make_worker(Path(temporary_directory), factory)

            worker._send_result_until_complete(
                InferenceResult(
                    "pi-001",
                    "gate-1",
                    datetime.now(timezone.utc),
                    8,
                    0.75,
                )
            )
            worker._heartbeat()

            self.assertEqual(factory.calls, [("http", "127.0.0.1", 8000, 1.0)])
            self.assertEqual(len(connection.requests), 2)
            method, path, body, headers = connection.requests[0]
            self.assertEqual((method, path), ("POST", "/api/inference-results"))
            self.assertEqual(headers["Content-Type"], "application/json")
            payload = json.loads(body.decode("utf-8"))
            self.assertEqual(payload["people_count"], 8)
            self.assertEqual(connection.requests[1][0:2], ("GET", "/health"))
            self.assertEqual(connection.close_count, 0)

    def test_reconnects_once_when_reused_connection_was_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            stale = _FakeConnection([RemoteDisconnected("idle connection closed")])
            replacement = _FakeConnection([_FakeResponse(200)])
            factory = _ConnectionFactory([stale, replacement])
            worker = self.make_worker(Path(temporary_directory), factory)

            worker._heartbeat()

            self.assertEqual(len(factory.calls), 2)
            self.assertEqual(stale.close_count, 1)
            self.assertEqual(replacement.requests[0][0:2], ("GET", "/health"))
            self.assertEqual(worker.consecutive_failures, 0)
            self.assertIs(worker._connection, replacement)

    def test_exhausted_reconnect_counts_as_one_operation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            connections = [
                _FakeConnection([RemoteDisconnected("offline")])
                for _ in range(6)
            ]
            factory = _ConnectionFactory(connections)
            worker = self.make_worker(Path(temporary_directory), factory)

            self.assertFalse(worker._perform_request("GET", "/health", "heartbeat"))
            self.assertEqual(worker.consecutive_failures, 1)
            self.assertFalse(worker._perform_request("GET", "/health", "heartbeat"))
            self.assertEqual(worker.consecutive_failures, 2)
            with self.assertRaises(CommunicationError):
                worker._perform_request("GET", "/health", "heartbeat")

            self.assertEqual(len(factory.calls), 6)
            self.assertTrue(all(item.close_count == 1 for item in connections))

    def test_stop_during_connection_error_does_not_count_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            stop_event = threading.Event()
            stop_event.set()
            connection = _FakeConnection([RemoteDisconnected("stopping")])
            worker = self.make_worker(
                Path(temporary_directory),
                _ConnectionFactory([connection]),
                stop_event,
            )

            self.assertFalse(
                worker._perform_request("GET", "/health", "heartbeat")
            )

            self.assertEqual(worker.consecutive_failures, 0)
            self.assertEqual(connection.close_count, 1)

    def test_invalid_port_is_reported_as_communication_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            environment = make_environment(Path(temporary_directory))
            environment = replace(
                environment,
                server=replace(
                    environment.server,
                    base_url="http://127.0.0.1:invalid",
                ),
            )

            with self.assertRaisesRegex(CommunicationError, "invalid server base URL"):
                CommunicationWorker(
                    environment=environment,
                    mailbox=ResultMailbox(),
                    stop_event=threading.Event(),
                    failure_queue=queue.Queue(),
                    verbose=False,
                )

    def test_worker_shutdown_closes_persistent_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            stop_event = threading.Event()
            connection = _FakeConnection([])
            worker = self.make_worker(
                Path(temporary_directory),
                _ConnectionFactory([connection]),
                stop_event,
            )
            worker._connection = connection
            stop_event.set()

            worker.run_worker()

            self.assertEqual(connection.close_count, 1)
            self.assertIsNone(worker._connection)


class _ConnectionFactory:
    def __init__(self, connections: list["_FakeConnection"]) -> None:
        self.connections = connections
        self.calls: list[tuple[str, str, int | None, float]] = []

    def __call__(
        self,
        scheme: str,
        host: str,
        port: int | None,
        timeout: float,
    ) -> "_FakeConnection":
        self.calls.append((scheme, host, port, timeout))
        return self.connections[len(self.calls) - 1]


class _FakeConnection:
    def __init__(self, responses: list["_FakeResponse | BaseException"]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str, bytes | None, dict[str, str]]] = []
        self.close_count = 0

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None,
        headers: dict[str, str],
    ) -> None:
        self.requests.append((method, path, body, headers))

    def getresponse(self) -> "_FakeResponse":
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def close(self) -> None:
        self.close_count += 1


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status = status_code
        self.will_close = False

    def read(self) -> bytes:
        return b"{}"

    def close(self) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
