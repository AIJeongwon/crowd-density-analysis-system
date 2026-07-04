from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def load_mock_sensor_client() -> ModuleType:
    root = Path(__file__).resolve().parents[1]
    module_path = root / "sensor-client" / "mock_sensor_client.py"
    spec = importlib.util.spec_from_file_location("mock_sensor_client", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load mock sensor client module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
