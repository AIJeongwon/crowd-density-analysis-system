from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.app import server as server_module
from backend.app.models import InferenceResult, LocationConfig, ValidationError
from backend.app.scoring import build_location_status
from backend.app.server import create_server, load_server_environment, main
from backend.app.store import InferenceStore


class ServerEnvironmentTest(unittest.TestCase):
    def test_cli_accepts_verbose_long_and_short_flags(self) -> None:
        parser = server_module.build_argument_parser()

        self.assertFalse(parser.parse_args([]).verbose)
        self.assertTrue(parser.parse_args(["--verbose"]).verbose)
        self.assertTrue(parser.parse_args(["-v"]).verbose)

    def test_log_timestamp_includes_two_digit_year_and_milliseconds(self) -> None:
        timestamp = datetime(2026, 8, 11, 12, 34, 56, 789123)
        self.assertEqual(
            server_module.format_log_timestamp(timestamp),
            "26-08-11 12:34:56.789",
        )

        output = io.StringIO()
        with (
            patch(
                "backend.app.server.format_log_timestamp",
                return_value="26-08-11 12:34:56.789",
            ),
            redirect_stdout(output),
        ):
            server_module.write_log("test message")

        self.assertEqual(
            output.getvalue(),
            "26-08-11 12:34:56.789 INFO [MainThread] test message\n",
        )

    def valid_payload(self) -> dict[str, object]:
        return {
            "backend": {"host": "0.0.0.0", "port": 8123},
            "locations": {
                "gate-1": {"area_m2": 40.0, "capacity": 20},
            },
        }

    def write_environment(
        self,
        directory: str,
        payload: object,
    ) -> Path:
        path = Path(directory) / "environment.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_loads_backend_and_location_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = load_server_environment(
                self.write_environment(directory, self.valid_payload())
            )

        self.assertEqual(environment.host, "0.0.0.0")
        self.assertEqual(environment.port, 8123)
        self.assertEqual(environment.location_configs["gate-1"].area_m2, 40.0)
        self.assertEqual(environment.location_configs["gate-1"].capacity, 20)

    def test_reports_file_and_json_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.json"
            with self.assertRaisesRegex(ValidationError, "cannot read"):
                load_server_environment(missing)

            malformed = Path(directory) / "environment.json"
            malformed.write_text('{"backend":', encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "invalid JSON"):
                load_server_environment(malformed)

    def test_rejects_invalid_backend_and_location_configuration(self) -> None:
        invalid_payloads = (
            ([], "environment root"),
            ({"locations": {}}, "backend must"),
            (
                {"backend": {"host": "", "port": 8000}, "locations": {}},
                "backend.host",
            ),
            (
                {"backend": {"host": "0.0.0.0", "port": True}, "locations": {}},
                "backend.port",
            ),
            (
                {"backend": {"host": "0.0.0.0", "port": 65536}, "locations": {}},
                "backend.port",
            ),
            (
                {"backend": {"host": "0.0.0.0", "port": 8000}},
                "locations must",
            ),
            (
                {
                    "backend": {"host": "0.0.0.0", "port": 8000},
                    "locations": {"gate-1": {"area_m2": 40, "capacity": 0}},
                },
                "capacity",
            ),
        )

        for payload, message in invalid_payloads:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as directory:
                path = self.write_environment(directory, payload)
                with self.assertRaisesRegex(ValidationError, message):
                    load_server_environment(path)

    def test_default_environment_path_is_next_to_server_module(self) -> None:
        self.assertEqual(
            server_module.DEFAULT_ENVIRONMENT_PATH,
            Path(server_module.__file__).resolve().with_name("environment.json"),
        )

    def test_main_loads_server_directory_environment_and_calls_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment_path = self.write_environment(
                directory,
                self.valid_payload(),
            )
            with (
                patch(
                    "backend.app.server.DEFAULT_ENVIRONMENT_PATH",
                    environment_path,
                ),
                patch("backend.app.server.run") as run_mock,
                patch("sys.argv", ["backend.app.server", "--verbose"]),
            ):
                main()

        run_mock.assert_called_once()
        args, kwargs = run_mock.call_args
        self.assertEqual(args, ("0.0.0.0", 8123))
        self.assertEqual(kwargs["location_configs"]["gate-1"].capacity, 20)
        self.assertTrue(kwargs["verbose"])

    def test_main_prints_clear_error_and_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment_path = Path(directory) / "environment.json"
            stderr = io.StringIO()
            with (
                patch(
                    "backend.app.server.DEFAULT_ENVIRONMENT_PATH",
                    environment_path,
                ),
                patch("sys.argv", ["backend.app.server"]),
                redirect_stderr(stderr),
                self.assertRaises(SystemExit) as raised,
            ):
                main()

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("cannot read", stderr.getvalue())
        self.assertIn("environment.json", stderr.getvalue())


class InferenceScoringTest(unittest.TestCase):
    location_id = "gate-1"
    config = LocationConfig(location_id, area_m2=10.0, capacity=20)

    def make_result(
        self,
        people_count: int,
        received_at: datetime,
        *,
        confidence: float = 0.9,
    ) -> InferenceResult:
        return InferenceResult(
            node_id="node-1",
            location_id=self.location_id,
            timestamp=received_at - timedelta(seconds=1),
            people_count=people_count,
            confidence=confidence,
            received_at=received_at,
        )

    def test_calculates_density_occupancy_and_congestion(self) -> None:
        now = datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc)
        status = build_location_status(
            self.location_id,
            [self.make_result(7, now, confidence=0.83)],
            self.config,
            now=now,
        )

        self.assertEqual(status["density_per_m2"], 0.7)
        self.assertEqual(status["occupancy_ratio"], 0.35)
        self.assertEqual(status["congestion_score"], 35.0)
        self.assertEqual(status["congestion_level"], "MEDIUM")
        self.assertEqual(status["confidence"], 0.83)

    def test_congestion_boundaries_and_latest_result(self) -> None:
        now = datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc)
        config = LocationConfig(self.location_id, area_m2=100.0, capacity=100)
        for count, expected in ((34, "LOW"), (35, "MEDIUM"), (69, "MEDIUM"), (70, "HIGH")):
            with self.subTest(count=count):
                status = build_location_status(
                    self.location_id,
                    [
                        self.make_result(99, now - timedelta(seconds=1)),
                        self.make_result(count, now),
                    ],
                    config,
                    now=now,
                )
                self.assertEqual(status["people_count"], count)
                self.assertEqual(status["congestion_level"], expected)

    def test_stale_and_unconfigured_locations_have_no_status(self) -> None:
        now = datetime(2026, 8, 10, 1, 0, tzinfo=timezone.utc)
        stale = self.make_result(5, now - timedelta(seconds=31))

        no_data = build_location_status(
            self.location_id,
            [stale],
            self.config,
            window_seconds=30,
            now=now,
        )
        unconfigured = build_location_status("unknown", [], None, now=now)

        self.assertEqual(no_data["status"], "NO_DATA")
        self.assertEqual(unconfigured["status"], "LOCATION_NOT_CONFIGURED")

    def test_validates_result_and_location_config(self) -> None:
        payload = {
            "node_id": "node-1",
            "location_id": self.location_id,
            "timestamp": "2026-08-10T01:00:00Z",
            "people_count": 4,
            "confidence": 0.75,
        }
        self.assertEqual(InferenceResult.from_payload(payload).people_count, 4)

        for key, value in (
            ("people_count", -1),
            ("people_count", 1.5),
            ("people_count", True),
            ("confidence", -0.01),
            ("confidence", 1.01),
            ("confidence", True),
            ("confidence", float("nan")),
        ):
            with self.subTest(key=key, value=value):
                invalid = dict(payload)
                invalid[key] = value
                with self.assertRaises(ValidationError):
                    InferenceResult.from_payload(invalid)


class InferenceApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InferenceStore()
        self.server = create_server(
            "127.0.0.1",
            0,
            store=self.store,
            location_configs={
                "gate-1": {"area_m2": 40.0, "capacity": 20},
                "gate-2": {"area_m2": 20.0, "capacity": 10},
            },
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def post_result(
        self,
        *,
        node_id: str = "node-1",
        location_id: str = "gate-1",
        people_count: int = 8,
    ) -> tuple[int, dict[str, object]]:
        return self.request(
            "/api/inference-results",
            method="POST",
            payload={
                "node_id": node_id,
                "location_id": location_id,
                "timestamp": "2026-08-10T01:00:00Z",
                "people_count": people_count,
                "confidence": 0.8,
            },
        )

    def test_health_post_recent_filters_and_removed_raw_endpoint(self) -> None:
        self.assertEqual(self.request("/health")[0], 200)
        status, created = self.post_result()
        self.assertEqual(status, 201)
        self.assertIn("received_at", created["result"])
        self.post_result(node_id="node-2", location_id="gate-2")

        status, recent = self.request(
            "/api/inference-results/recent?location_id=gate-1&node_id=node-1"
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(recent["results"]), 1)
        self.assertEqual(recent["results"][0]["node_id"], "node-1")
        self.assertEqual(self.request("/api/sensor-readings", method="POST", payload={})[0], 404)

    def test_location_status_uses_latest_inference(self) -> None:
        self.post_result(people_count=8)
        status, body = self.request("/api/locations/gate-1/status")

        self.assertEqual(status, 200)
        self.assertEqual(body["people_count"], 8)
        self.assertEqual(body["density_per_m2"], 0.2)
        self.assertEqual(body["occupancy_ratio"], 0.4)
        self.assertEqual(body["congestion_score"], 40.0)
        self.assertEqual(body["congestion_level"], "MEDIUM")

    def test_rejects_invalid_inference_result(self) -> None:
        status, body = self.request(
            "/api/inference-results",
            method="POST",
            payload={
                "node_id": "node-1",
                "location_id": "gate-1",
                "timestamp": "2026-08-10T01:00:00Z",
                "people_count": -1,
                "confidence": 0.8,
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "validation_error")

    def test_default_mode_suppresses_verbose_access_logs(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            self.assertEqual(self.request("/health")[0], 200)

        self.assertEqual(output.getvalue(), "")

    def test_verbose_logs_access_and_validated_inference_payload(self) -> None:
        self.server.verbose = True
        output = io.StringIO()
        payload = {
            "node_id": "node-verbose",
            "location_id": "gate-1",
            "timestamp": "2026-08-10T01:00:00Z",
            "people_count": 9,
            "confidence": 0.85,
            "secret": "must-not-be-logged",
        }

        with redirect_stdout(output):
            self.assertEqual(self.request("/health")[0], 200)
            self.assertEqual(
                self.request(
                    "/api/inference-results",
                    method="POST",
                    payload=payload,
                )[0],
                201,
            )

        logs = output.getvalue()
        self.assertIn("DEBUG", logs)
        self.assertIn('"GET /health HTTP/1.1" 200', logs)
        self.assertIn("incoming inference payload", logs)
        self.assertIn('"people_count":9', logs)
        self.assertIn("received inference result", logs)
        self.assertNotIn("must-not-be-logged", logs)


if __name__ == "__main__":
    unittest.main()
