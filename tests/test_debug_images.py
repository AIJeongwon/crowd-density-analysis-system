from __future__ import annotations

import importlib.util
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from types import ModuleType


def load_debug_images() -> ModuleType:
    module_path = (
        Path(__file__).resolve().parents[1] / "sensor-client" / "debug_images.py"
    )
    spec = importlib.util.spec_from_file_location("debug_images", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load debug_images module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


debug_images = load_debug_images()


def decode_rgb_png(data: bytes) -> tuple[int, int, bytes]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("invalid PNG signature")
    offset = 8
    width = height = 0
    compressed = bytearray()
    while offset < len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if chunk_type == b"IHDR":
            width, height, depth, color_type, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", chunk_data)
            )
            if (depth, color_type, compression, filtering, interlace) != (
                8,
                2,
                0,
                0,
                0,
            ):
                raise AssertionError("PNG is not non-interlaced 8-bit RGB")
        elif chunk_type == b"IDAT":
            compressed.extend(chunk_data)
        elif chunk_type == b"IEND":
            break
    scanlines = zlib.decompress(compressed)
    row_size = width * 3
    rows = []
    for offset in range(0, len(scanlines), row_size + 1):
        if scanlines[offset] != 0:
            raise AssertionError("unexpected PNG filter")
        rows.append(scanlines[offset + 1 : offset + row_size + 1])
    return width, height, b"".join(rows)


class DebugImagesTest(unittest.TestCase):
    def test_thermal_png_is_rgb_with_thermal_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "nested" / "thermal.png"
            result = debug_images.save_thermal_png(
                [10, 20, 30, 40], 2, 2, destination
            )

            self.assertEqual(result, destination)
            width, height, rgb = decode_rgb_png(destination.read_bytes())
            self.assertEqual((width, height), (2, 2))
            self.assertEqual(rgb[:3], b"\x00\x00\x00")
            self.assertEqual(rgb[-3:], b"\xff\xff\xff")
            self.assertEqual(list(destination.parent.glob("*.tmp")), [])

    def test_thermal_png_validates_dimensions_length_and_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "thermal.png"
            invalid_cases = (
                ([1], 0, 1),
                ([1], True, 1),
                ([1], 1, -1),
                ([1], 2, 1),
                ([False], 1, 1),
                ([float("nan")], 1, 1),
            )
            for pixels, width, height in invalid_cases:
                with self.subTest(pixels=pixels, width=width, height=height):
                    with self.assertRaises(ValueError):
                        debug_images.save_thermal_png(
                            pixels, width, height, destination
                        )

    def test_lidar_png_has_black_background_and_white_projected_points(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "nested" / "lidar.png"
            points = ([0, 1000, 15], [90, 500, 10], [180, 2000, 5])
            result = debug_images.save_lidar_png(
                points,
                destination,
                width=5,
                height=5,
                max_distance_m=1.0,
            )

            self.assertEqual(result, destination)
            width, height, rgb = decode_rgb_png(destination.read_bytes())
            self.assertEqual((width, height), (5, 5))
            top = (0 * width + 2) * 3
            right = (2 * width + 3) * 3
            bottom_right = (4 * width + 4) * 3
            self.assertEqual(rgb[top : top + 3], b"\xff\xff\xff")
            self.assertEqual(rgb[right : right + 3], b"\xff\xff\xff")
            self.assertEqual(rgb[bottom_right : bottom_right + 3], b"\x00\x00\x00")

    def test_lidar_png_validates_configuration_and_samples(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "lidar.png"
            invalid_calls = (
                (([0, 100, 1],), {"width": 0}),
                (([0, 100, 1],), {"height": False}),
                (([0, 100, 1],), {"max_distance_m": 0}),
                (([0, 100],), {}),
                ((["north", 100, 1],), {}),
                (([0, -1, 1],), {}),
                (([0, 100, float("inf")],), {}),
            )
            for points, kwargs in invalid_calls:
                with self.subTest(points=points, kwargs=kwargs):
                    with self.assertRaises(ValueError):
                        debug_images.save_lidar_png(
                            points, destination, **kwargs
                        )


if __name__ == "__main__":
    unittest.main()
