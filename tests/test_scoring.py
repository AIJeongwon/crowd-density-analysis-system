from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from backend.app.models import SensorReading, ValidationError
from backend.app.scoring import build_location_status
from sensor_client_import import load_mock_sensor_client


class ScoringTest(unittest.TestCase):
    def test_combines_thermal_and_lidar_readings(self) -> None:
        now = datetime(2026, 7, 4, 1, 0, tzinfo=timezone.utc)
        readings = [
            SensorReading(
                device_id="thermal-node-001",
                location_id="moran-market-gate-1",
                sensor_type="thermal",
                timestamp=now,
                metrics={"hotspot_count": 10, "valid": True},
                received_at=now,
            ),
            SensorReading(
                device_id="lidar-node-001",
                location_id="moran-market-gate-1",
                sensor_type="lidar",
                timestamp=now,
                metrics={"object_count": 15, "avg_distance": 2.5, "valid": True},
                received_at=now,
            ),
        ]

        status = build_location_status(
            "moran-market-gate-1",
            readings,
            window_seconds=30,
            now=now,
        )

        self.assertEqual(status["status"], "OK")
        self.assertEqual(status["congestion_level"], "MEDIUM")
        self.assertEqual(status["confidence"], 0.9)
        self.assertIn("thermal", status["sensors"])
        self.assertIn("lidar", status["sensors"])

    def test_returns_no_data_when_window_has_no_readings(self) -> None:
        now = datetime(2026, 7, 4, 1, 0, tzinfo=timezone.utc)

        status = build_location_status(
            "moran-market-gate-1",
            [],
            window_seconds=30,
            now=now,
        )

        self.assertEqual(status["status"], "NO_DATA")
        self.assertEqual(status["congestion_level"], "UNKNOWN")
        self.assertEqual(status["confidence"], 0.0)

    def test_uses_received_at_for_freshness(self) -> None:
        now = datetime(2026, 7, 4, 1, 0, tzinfo=timezone.utc)
        reading = SensorReading(
            device_id="thermal-node-001",
            location_id="moran-market-gate-1",
            sensor_type="thermal",
            timestamp=now - timedelta(hours=1),
            metrics={"hotspot_count": 10, "valid": True},
            received_at=now,
        )

        status = build_location_status(
            "moran-market-gate-1",
            [reading],
            window_seconds=30,
            now=now,
        )

        self.assertEqual(status["status"], "OK")
        self.assertIn("thermal", status["sensors"])

    def test_rejects_non_boolean_valid_metric(self) -> None:
        payload = {
            "device_id": "thermal-node-001",
            "location_id": "moran-market-gate-1",
            "sensor_type": "thermal",
            "timestamp": "2026-07-04T01:00:00+00:00",
            "metrics": {"hotspot_count": 10, "valid": "false"},
        }

        with self.assertRaises(ValidationError):
            SensorReading.from_payload(payload)


class MockSensorClientTest(unittest.TestCase):
    def test_both_sensor_type_counts_cycles_not_payloads(self) -> None:
        client = load_mock_sensor_client()
        payloads = client._build_payloads("both", None, "moran-market-gate-1")

        self.assertEqual(len(payloads), 2)
        self.assertEqual({payload["sensor_type"] for payload in payloads}, {"thermal", "lidar"})

    def test_count_is_deprecated_alias_for_cycles(self) -> None:
        client = load_mock_sensor_client()

        self.assertEqual(client._resolve_cycles(None, 3), 3)
        self.assertEqual(client._resolve_cycles(5, 3), 5)
        self.assertEqual(client._resolve_cycles(None, None), 0)


if __name__ == "__main__":
    unittest.main()
