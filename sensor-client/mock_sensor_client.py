from __future__ import annotations

# Direct execution is a compatibility entry point for the sensor runtime.
if __name__ == "__main__":
    from sensor_client import main as _sensor_main

    raise SystemExit(_sensor_main())


import argparse
import json
import random
import time
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import Request, urlopen


def build_thermal_payload(device_id: str, location_id: str) -> dict:
    hotspot_count = random.randint(0, 18)
    avg_temp = round(random.uniform(22.0, 33.5), 1)
    max_temp = round(avg_temp + random.uniform(2.0, 6.0), 1)
    return {
        "device_id": device_id,
        "location_id": location_id,
        "sensor_type": "thermal",
        "timestamp": _now_iso(),
        "metrics": {
            "hotspot_count": hotspot_count,
            "avg_temp": avg_temp,
            "max_temp": max_temp,
            "valid": True,
        },
    }


def build_lidar_payload(device_id: str, location_id: str) -> dict:
    object_count = random.randint(0, 24)
    avg_distance = round(random.uniform(0.8, 5.0), 2)
    min_distance = round(max(0.2, avg_distance - random.uniform(0.2, 0.8)), 2)
    return {
        "device_id": device_id,
        "location_id": location_id,
        "sensor_type": "lidar",
        "timestamp": _now_iso(),
        "metrics": {
            "object_count": object_count,
            "avg_distance": avg_distance,
            "min_distance": min_distance,
            "valid": True,
        },
    }


def post_payload(server_url: str, payload: dict) -> None:
    endpoint = server_url.rstrip("/") + "/api/sensor-readings"
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=5) as response:
        response.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="Send mock sensor readings to the backend.")
    parser.add_argument("--server-url", default="http://127.0.0.1:8000")
    parser.add_argument("--location-id", default="moran-market-gate-1")
    parser.add_argument(
        "--sensor-type",
        choices=["thermal", "lidar", "both"],
        default="both",
    )
    parser.add_argument("--device-id", default=None)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--cycles", type=int, default=None, help="0 means run forever")
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="deprecated alias for --cycles",
    )
    args = parser.parse_args()

    cycles = _resolve_cycles(args.cycles, args.count)
    cycle = 0
    while cycles == 0 or cycle < cycles:
        for payload in _build_payloads(args.sensor_type, args.device_id, args.location_id):
            try:
                post_payload(args.server_url, payload)
                print(
                    f"sent {payload['sensor_type']} reading "
                    f"from {payload['device_id']} to {args.server_url}"
                )
            except URLError as exc:
                print(f"failed to send reading: {exc}")
        cycle += 1
        if cycles == 0 or cycle < cycles:
            time.sleep(args.interval)


def _build_payloads(sensor_type: str, device_id: str | None, location_id: str) -> list[dict]:
    if sensor_type == "thermal":
        return [build_thermal_payload(device_id or "thermal-node-001", location_id)]
    if sensor_type == "lidar":
        return [build_lidar_payload(device_id or "lidar-node-001", location_id)]
    return [
        build_thermal_payload("thermal-node-001", location_id),
        build_lidar_payload("lidar-node-001", location_id),
    ]


def _resolve_cycles(cycles: int | None, count: int | None) -> int:
    if cycles is not None:
        return max(0, cycles)
    if count is not None:
        return max(0, count)
    return 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
