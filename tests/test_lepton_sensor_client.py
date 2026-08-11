from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import ModuleType
from urllib.error import URLError
from urllib.request import Request

from backend.app.models import SensorReading


def load_lepton_sensor_client() -> ModuleType:
    root = Path(__file__).resolve().parents[1]
    module_path = root / "sensor-client" / "lepton_sensor_client.py"
    spec = importlib.util.spec_from_file_location("lepton_sensor_client", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load Lepton sensor client module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LeptonSensorClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client_module = load_lepton_sensor_client()

    def test_normalizes_server_ip(self) -> None:
        self.assertEqual(
            self.client_module.normalize_server_endpoint("192.168.0.10"),
            "http://192.168.0.10:8000/api/sensor-readings",
        )
        self.assertEqual(
            self.client_module.normalize_server_endpoint("http://127.0.0.1:9000"),
            "http://127.0.0.1:9000/api/sensor-readings",
        )

    def test_parses_long_and_short_cli_options(self) -> None:
        parser = self.client_module.build_argument_parser()

        long_args = parser.parse_args(
            [
                "--ip", "192.168.0.10",
                "--dev", "/dev/video0",
                "--interval", "5",
                "--verbose",
            ]
        )
        self.assertEqual(long_args.server_ip, "192.168.0.10")
        self.assertEqual(long_args.dev_path, "/dev/video0")
        self.assertEqual(long_args.interval, 5.0)
        self.assertTrue(long_args.verbose)

        short_args = parser.parse_args(
            ["-i", "127.0.0.1", "-d", "/dev/video1", "-t", "1.5"]
        )
        self.assertEqual(short_args.server_ip, "127.0.0.1")
        self.assertEqual(short_args.dev_path, "/dev/video1")
        self.assertEqual(short_args.interval, 1.5)
        self.assertFalse(short_args.verbose)

        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["192.168.0.10", "/dev/video0", "5", "verbose"])

    def test_captures_y16_and_builds_server_compatible_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_dir = Path(temp_dir)

            def fake_runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                output_arg = next(arg for arg in command if arg.startswith("--stream-to="))
                output_path = Path(output_arg.split("=", 1)[1])
                output_path.write_bytes((30000).to_bytes(2, "little") * 19200)
                return subprocess.CompletedProcess(command, 0, "", "")

            client = self.client_module.LeptonSensorClient(
                "192.168.0.10",
                "/dev/video-test",
                5,
                False,
                work_dir=work_dir,
                runner=fake_runner,
            )

            frame_path = client.capture_once()
            payload = client.build_payload(frame_path)

            self.assertEqual(frame_path.stat().st_size, 38400)
            self.assertEqual(payload["sensor_type"], "thermal")
            self.assertEqual(payload["metrics"]["avg_temp"], 26.85)
            self.assertEqual(payload["metrics"]["min_temp"], 26.85)
            self.assertEqual(payload["metrics"]["max_temp"], 26.85)
            self.assertEqual(len(payload["metrics"]["pixels"]), 19200)
            SensorReading.from_payload(payload)

    def test_successful_delivery_removes_temporary_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            frame_path = Path(temp_dir) / "frame.y16"
            frame_path.write_bytes((29815).to_bytes(2, "little") * 19200)
            captured_requests: list[Request] = []

            def fake_open(request: Request, **_: object) -> _FakeResponse:
                captured_requests.append(request)
                return _FakeResponse(201, b'{"reading":{"sensor_type":"thermal"}}')

            client = self.client_module.LeptonSensorClient(
                "127.0.0.1",
                "/dev/video0",
                1,
                False,
                work_dir=Path(temp_dir),
                opener=fake_open,
            )

            client.deliver(frame_path)

            self.assertFalse(frame_path.exists())
            payload = json.loads(captured_requests[0].data.decode("utf-8"))
            self.assertEqual(payload["metrics"]["pixels"][0], 29815)
            self.assertEqual(payload["metrics"]["avg_temp"], 25.0)

    def test_failed_delivery_retains_temporary_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            frame_path = Path(temp_dir) / "frame.y16"
            frame_path.write_bytes((29815).to_bytes(2, "little") * 19200)

            def offline(*_: object, **__: object) -> None:
                raise URLError("offline")

            client = self.client_module.LeptonSensorClient(
                "127.0.0.1",
                "/dev/video0",
                1,
                False,
                work_dir=Path(temp_dir),
                opener=offline,
            )

            with self.assertRaises(self.client_module.UploadError):
                client.deliver(frame_path)

            self.assertTrue(frame_path.exists())

    def test_logs_only_in_verbose_mode(self) -> None:
        quiet_client = self.client_module.LeptonSensorClient(
            "127.0.0.1", "/dev/video0", 1, False
        )
        verbose_client = self.client_module.LeptonSensorClient(
            "127.0.0.1", "/dev/video0", 1, True
        )

        with redirect_stderr(io.StringIO()) as quiet_output:
            quiet_client.log("hidden")
        with redirect_stderr(io.StringIO()) as verbose_output:
            verbose_client.log("shown")

        self.assertEqual(quiet_output.getvalue(), "")
        self.assertIn("shown", verbose_output.getvalue())
        self.assertRegex(
            verbose_output.getvalue(),
            r"^\[\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\] ",
        )


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
