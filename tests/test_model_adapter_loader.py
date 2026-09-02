from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


SENSOR_CLIENT_DIR = Path(__file__).resolve().parents[1] / "sensor-client"
if str(SENSOR_CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(SENSOR_CLIENT_DIR))

from model_adapter_thread import (  # noqa: E402
    load_model_adapter,
    to_model_input,
)


class ModelAdapterLoaderTest(unittest.TestCase):
    def test_loads_dataclass_based_adapter_module(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            adapter_path = root / "adapter.py"
            adapter_path.write_text(
                "from __future__ import annotations\n"
                "from dataclasses import dataclass\n"
                "\n"
                "@dataclass(frozen=True)\n"
                "class Settings:\n"
                "    confidence: float = 0.25\n"
                "\n"
                "class ModelAdapter:\n"
                "    def __init__(self, model_path):\n"
                "        self.settings = Settings()\n"
                "        self.model_path = model_path\n"
                "\n"
                "    def infer(self, sensor_data):\n"
                "        return {\"people_count\": 0, \"confidence\": 0.0}\n",
                encoding="utf-8",
            )
            model_path = root / "model.onnx"
            model_path.write_bytes(b"model")

            loaded = load_model_adapter(adapter_path, model_path)

            self.assertEqual(loaded.settings.confidence, 0.25)
            self.assertEqual(loaded.model_path, model_path)

    def test_failed_import_removes_partial_module_registration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            adapter_path = root / "adapter.py"
            adapter_path.write_text(
                "raise RuntimeError('broken import')\n",
                encoding="utf-8",
            )
            model_path = root / "model.onnx"
            model_path.write_bytes(b"model")

            with self.assertRaisesRegex(Exception, "failed to import"):
                load_model_adapter(adapter_path, model_path)

            self.assertNotIn("cdas_model_adapter", sys.modules)


    def test_model_input_includes_debug_output_directory(self) -> None:
        now = datetime(2026, 9, 2, 8, 30, tzinfo=timezone.utc)
        fused = SimpleNamespace(
            fused_at=now,
            thermal=SimpleNamespace(
                captured_at=now,
                width=120,
                height=160,
                pixels=(30000,),
            ),
            lidar=SimpleNamespace(
                captured_at=now,
                sequence=1,
                points=(),
            ),
        )

        payload = to_model_input(
            fused,
            debug=True,
            debug_dir=Path("/tmp/cdas"),
        )

        self.assertEqual(
            payload["debug"],
            {
                "enabled": True,
                "output_dir": "/tmp/cdas",
            },
        )


if __name__ == "__main__":
    unittest.main()
