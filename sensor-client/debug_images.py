"""Standard-library image helpers for sensor debugging."""

from __future__ import annotations

import math
import os
import struct
import tempfile
import zlib
from collections.abc import Iterable
from numbers import Real
from os import PathLike
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def save_thermal_png(
    pixels: Iterable[object],
    width: int,
    height: int,
    path: str | PathLike[str],
) -> Path:
    """Save scalar thermal pixels as an RGB PNG using a thermal palette."""

    _validate_dimension(width, "width")
    _validate_dimension(height, "height")
    values = _numeric_values(pixels, "pixels")
    expected = width * height
    if len(values) != expected:
        raise ValueError(f"pixels must contain exactly {expected} values")

    minimum = min(values)
    maximum = max(values)
    if maximum == minimum:
        intensities = [0] * expected
    else:
        scale = 255.0 / (maximum - minimum)
        intensities = [
            max(0, min(255, round((value - minimum) * scale)))
            for value in values
        ]

    rgb = bytearray()
    for intensity in intensities:
        rgb.extend(_thermal_rgb(intensity))

    destination = Path(path)
    _atomic_write(destination, _encode_rgb_png(width, height, rgb))
    return destination


def save_lidar_png(
    points: Iterable[object],
    path: str | PathLike[str],
    *,
    width: int = 640,
    height: int = 640,
    max_distance_m: float = 12.0,
) -> Path:
    """Save ``[angle_deg, distance_mm, quality]`` samples as an RGB PNG."""

    _validate_dimension(width, "width")
    _validate_dimension(height, "height")
    maximum_distance = _numeric_value(max_distance_m, "max_distance_m")
    if maximum_distance <= 0:
        raise ValueError("max_distance_m must be greater than zero")

    try:
        iterator = iter(points)
    except TypeError as exc:
        raise ValueError("points must be an iterable of lidar samples") from exc

    rgb = bytearray(width * height * 3)
    center_x = (width - 1) / 2.0
    center_y = (height - 1) / 2.0
    image_radius = (min(width, height) - 1) / 2.0
    maximum_distance_mm = maximum_distance * 1000.0

    for index, point in enumerate(iterator):
        try:
            sample = tuple(point)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ValueError(f"points[{index}] must contain three values") from exc
        if len(sample) != 3:
            raise ValueError(f"points[{index}] must contain three values")

        angle = _numeric_value(sample[0], f"points[{index}].angle_deg")
        distance = _numeric_value(sample[1], f"points[{index}].distance_mm")
        _numeric_value(sample[2], f"points[{index}].quality")
        if distance < 0:
            raise ValueError(f"points[{index}].distance_mm cannot be negative")
        if distance > maximum_distance_mm:
            continue

        radius = (distance / maximum_distance_mm) * image_radius
        angle_radians = math.radians(angle)
        x = round(center_x + math.sin(angle_radians) * radius)
        y = round(center_y - math.cos(angle_radians) * radius)
        x = max(0, min(width - 1, x))
        y = max(0, min(height - 1, y))
        offset = (y * width + x) * 3
        rgb[offset : offset + 3] = b"\xff\xff\xff"

    destination = Path(path)
    _atomic_write(destination, _encode_rgb_png(width, height, rgb))
    return destination


def _validate_dimension(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _numeric_values(values: Iterable[object], name: str) -> list[float]:
    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ValueError(f"{name} must be an iterable of numbers") from exc
    return [
        _numeric_value(value, f"{name}[{index}]")
        for index, value in enumerate(iterator)
    ]


def _numeric_value(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    try:
        converted = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be a finite number")
    return converted


def _thermal_rgb(intensity: int) -> tuple[int, int, int]:
    palette = (
        (0, (0, 0, 0)),
        (32, (0, 0, 96)),
        (80, (72, 0, 160)),
        (128, (192, 0, 96)),
        (176, (255, 64, 0)),
        (224, (255, 200, 0)),
        (255, (255, 255, 255)),
    )
    for index in range(1, len(palette)):
        lower_value, lower_color = palette[index - 1]
        upper_value, upper_color = palette[index]
        if intensity <= upper_value:
            ratio = (intensity - lower_value) / (upper_value - lower_value)
            return tuple(
                round(lower + (upper - lower) * ratio)
                for lower, upper in zip(lower_color, upper_color)
            )
    return palette[-1][1]


def _encode_rgb_png(width: int, height: int, rgb: bytes | bytearray) -> bytes:
    expected = width * height * 3
    if len(rgb) != expected:
        raise ValueError(f"RGB data must contain exactly {expected} bytes")
    row_size = width * 3
    scanlines = b"".join(
        b"\x00" + rgb[offset : offset + row_size]
        for offset in range(0, len(rgb), row_size)
    )
    return b"".join(
        (
            PNG_SIGNATURE,
            _png_chunk(
                b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
            ),
            _png_chunk(b"IDAT", zlib.compress(scanlines)),
            _png_chunk(b"IEND", b""),
        )
    )


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", checksum)
    )


def _atomic_write(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(payload)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, destination)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
