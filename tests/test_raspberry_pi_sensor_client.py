from __future__ import annotations

import importlib.util
import io
import json
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from backend.app import server
from backend.app.store import ReadingStore


def load_raspberry_pi_sensor_client() -> ModuleType:
    root = Path(__file__).resolve().parents[1]
    module_path = root / "sensor-client" / "raspberry_pi_sensor_client.py"
    spec = importlib.util.spec_from_file_location(
        "raspberry_pi_sensor_client",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load Raspberry Pi sensor client module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RaspberryPiSensorClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client_module = load_raspberry_pi_sensor_client()

    def test_builds_utc_payload_and_rejects_nan(self) -> None:
        client = self.client_module.SensorClient(
            "http://example.test/",
            "thermal-pi-001",
            "moran-market-gate-1",
        )
        measured_at = datetime(2026, 7, 26, 18, 0, tzinfo=timezone.utc)

        payload = client.build_payload(
            "thermal",
            {"hotspot_count": 8, "valid": True},
            measured_at=measured_at,
        )

        self.assertEqual(payload["device_id"], "thermal-pi-001")
        self.assertEqual(payload["location_id"], "moran-market-gate-1")
        self.assertEqual(payload["sensor_type"], "thermal")
        self.assertEqual(payload["timestamp"], "2026-07-26T18:00:00+00:00")
        with self.assertRaises(ValueError):
            client.build_payload("thermal", {"hotspot_count": float("nan")})

    def test_retries_temporary_network_failure(self) -> None:
        client = self.client_module.SensorClient(
            "http://example.test",
            "lidar-pi-001",
            "moran-market-gate-1",
            max_attempts=2,
            retry_delay=0.25,
        )
        response = _FakeResponse(
            201,
            b'{"reading":{"sensor_type":"lidar"}}',
        )
        sleep_calls: list[float] = []

        with patch.object(
            self.client_module,
            "urlopen",
            side_effect=[self.client_module.URLError("offline"), response],
        ):
            client._sleep = sleep_calls.append
            result = client.send(
                "lidar",
                {"object_count": 5, "avg_distance": 2.1, "valid": True},
            )

        self.assertEqual(result["reading"]["sensor_type"], "lidar")
        self.assertEqual(sleep_calls, [0.25])

    def test_sends_reading_to_backend(self) -> None:
        original_store = server.STORE
        server.STORE = ReadingStore()
        http_server = ThreadingHTTPServer(("127.0.0.1", 0), server.RequestHandler)
        thread = threading.Thread(target=http_server.serve_forever, daemon=True)
        thread.start()

        try:
            port = http_server.server_address[1]
            client = self.client_module.SensorClient(
                f"http://127.0.0.1:{port}",
                "thermal-pi-001",
                "moran-market-gate-1",
                max_attempts=1,
            )
            result = client.send(
                "thermal",
                {
                    "hotspot_count": 9,
                    "avg_temp": 27.5,
                    "max_temp": 32.1,
                    "valid": True,
                },
            )

            self.assertEqual(result["reading"]["device_id"], "thermal-pi-001")
            stored = server.STORE.recent(location_id="moran-market-gate-1")
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0].metrics["hotspot_count"], 9)
        finally:
            http_server.shutdown()
            http_server.server_close()
            thread.join(timeout=2)
            server.STORE = original_store

    def test_cli_sends_one_metrics_object(self) -> None:
        response = {
            "reading": {
                "device_id": "thermal-pi-001",
                "sensor_type": "thermal",
            }
        }
        argv = [
            "raspberry_pi_sensor_client.py",
            "--server-url",
            "http://example.test",
            "--location-id",
            "moran-market-gate-1",
            "--sensor-type",
            "thermal",
            "--device-id",
            "thermal-pi-001",
            "--metrics-json",
            '{"hotspot_count":7,"valid":true}',
        ]

        with (
            patch.object(self.client_module.sys, "argv", argv),
            patch.object(
                self.client_module.SensorClient,
                "send",
                return_value=response,
            ) as send,
            redirect_stdout(io.StringIO()) as stdout,
            redirect_stderr(io.StringIO()),
        ):
            exit_code = self.client_module.main()

        self.assertEqual(exit_code, 0)
        send.assert_called_once_with(
            "thermal",
            {"hotspot_count": 7, "valid": True},
        )
        self.assertEqual(json.loads(stdout.getvalue()), response)


class _FakeResponse:
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self.body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body

    def getcode(self) -> int:
        return self.status_code


if __name__ == "__main__":
    unittest.main()
