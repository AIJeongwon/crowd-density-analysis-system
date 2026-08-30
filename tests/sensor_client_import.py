from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_mock_sensor_client() -> ModuleType:
    return load_sensor_client_module(
        "mock_sensor_client.py",
        "mock_sensor_client",
    )


def load_thermal_camera() -> ModuleType:
    return load_sensor_client_module(
        "thermal_camera.py",
        "thermal_camera",
    )


def load_thermal_person_detector() -> ModuleType:
    load_thermal_camera()
    return load_sensor_client_module(
        "thermal_person_detector.py",
        "thermal_person_detector",
    )


def load_sensor_client_module(filename: str, module_name: str) -> ModuleType:
    root = Path(__file__).resolve().parents[1]
    module_path = root / "sensor-client" / filename
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load sensor client module: {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
