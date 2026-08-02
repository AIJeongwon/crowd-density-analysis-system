from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


CENTIKELVIN_OFFSET = 27315.0
DEVICE_PATH_PATTERN = re.compile(r"^/dev/[A-Za-z0-9_./-]+$")
FORMAT_PATTERN = re.compile(r"^\s*\[\d+\]:\s+'([^']+)'")
SIZE_PATTERN = re.compile(r"Size:\s+Discrete\s+(\d+)x(\d+)")
MINIMUM_AUTO_DISPLAY_SPAN = 2.0
TEMPORAL_FILTER_ALPHA = 0.35
MOTION_THRESHOLD_CELSIUS = 1.0


@dataclass(frozen=True)
class TemperatureStatistics:
    center: float
    selected: float
    minimum: float
    maximum: float
    average: float
    minimum_point: tuple[int, int]
    maximum_point: tuple[int, int]


def centikelvin_to_celsius(value: float) -> float:
    return (value - CENTIKELVIN_OFFSET) / 100.0


def validate_temperature_range(
    minimum: float | None,
    maximum: float | None,
    minimum_option: str = "--min-temp",
    maximum_option: str = "--max-temp",
) -> tuple[float, float] | None:
    if minimum is None and maximum is None:
        return None
    if minimum is None or maximum is None:
        raise ValueError(
            f"{minimum_option}와 {maximum_option}는 함께 지정해야 합니다."
        )
    if minimum >= maximum:
        raise ValueError(
            f"{minimum_option}는 {maximum_option}보다 작아야 합니다."
        )
    return minimum, maximum


def validate_device_path(device: str) -> str:
    if not DEVICE_PATH_PATTERN.fullmatch(device):
        raise ValueError("장치 경로는 /dev/video0 또는 /dev/v4l/by-id/... 형식이어야 합니다.")
    return device


def parse_y16_resolutions(output: str) -> list[tuple[int, int]]:
    current_format = ""
    resolutions: list[tuple[int, int]] = []

    for line in output.splitlines():
        format_match = FORMAT_PATTERN.search(line)
        if format_match:
            current_format = format_match.group(1).strip()
            continue

        size_match = SIZE_PATTERN.search(line)
        if current_format == "Y16" and size_match:
            resolution = (int(size_match.group(1)), int(size_match.group(2)))
            if resolution not in resolutions:
                resolutions.append(resolution)

    return resolutions


def select_y16_resolution(
    resolutions: list[tuple[int, int]],
) -> tuple[int, int]:
    if not resolutions:
        raise RuntimeError("장치에서 Y16 해상도를 찾지 못했습니다.")

    image_resolutions = [
        resolution
        for resolution in resolutions
        if resolution[0] * 3 == resolution[1] * 4
    ]
    candidates = image_resolutions or resolutions
    return max(candidates, key=lambda resolution: resolution[0] * resolution[1])


def detect_y16_resolution(device: str) -> tuple[int, int]:
    result = subprocess.run(
        ["v4l2-ctl", "-d", device, "--list-formats-ext"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or "v4l2-ctl 실행 실패"
        raise RuntimeError(f"카메라 형식을 확인하지 못했습니다: {message}")
    return select_y16_resolution(parse_y16_resolutions(result.stdout))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PureThermal 3의 Y16 영상을 온도와 함께 실시간으로 표시합니다."
    )
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--scale", type=int, default=4)
    parser.add_argument(
        "--backend",
        choices=["auto", "v4l2", "gstreamer"],
        default="auto",
    )
    parser.add_argument(
        "--palette",
        choices=["inferno", "turbo", "jet", "hot"],
        default="inferno",
    )
    parser.add_argument(
        "--display-mode",
        choices=["smooth", "raw"],
        default="smooth",
    )
    parser.add_argument("--min-temp", type=float)
    parser.add_argument("--max-temp", type=float)
    parser.add_argument(
        "--anchor-min-temp",
        type=float,
    )
    parser.add_argument(
        "--anchor-max-temp",
        type=float,
    )
    parser.add_argument(
        "--rotate",
        type=int,
        choices=[0, 90, 180, 270],
        default=0,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs") / "thermal",
    )
    return parser


def _load_runtime_dependencies() -> tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "OpenCV와 NumPy가 필요합니다. "
            "'sudo apt install python3-opencv python3-numpy'를 실행해 주세요."
        ) from exc
    return cv2, np


def decode_y16_frame(frame: Any, width: int, height: int, np: Any) -> Any:
    squeezed = np.squeeze(frame)

    if squeezed.dtype == np.uint16 and squeezed.shape == (height, width):
        return squeezed.copy()

    if squeezed.dtype == np.uint8 and squeezed.size == width * height * 2:
        contiguous = np.ascontiguousarray(squeezed)
        return np.frombuffer(contiguous.tobytes(), dtype="<u2").reshape(height, width)

    raise RuntimeError(
        "Y16 프레임을 받지 못했습니다. "
        f"수신 형식: dtype={squeezed.dtype}, shape={squeezed.shape}"
    )


def raw_to_temperature(raw_frame: Any, np: Any) -> Any:
    return (raw_frame.astype(np.float32) - CENTIKELVIN_OFFSET) / 100.0


def rotate_frame(frame: Any, degrees: int, np: Any) -> Any:
    turns = {0: 0, 90: 3, 180: 2, 270: 1}[degrees]
    if turns == 0:
        return frame
    return np.rot90(frame, k=turns).copy()


def calculate_statistics(
    temperature_frame: Any,
    selected_point: tuple[int, int] | None,
    np: Any,
) -> TemperatureStatistics:
    height, width = temperature_frame.shape
    center_point = (width // 2, height // 2)
    point = selected_point or center_point
    x = min(max(point[0], 0), width - 1)
    y = min(max(point[1], 0), height - 1)

    minimum_index = int(np.argmin(temperature_frame))
    maximum_index = int(np.argmax(temperature_frame))
    minimum_y, minimum_x = np.unravel_index(minimum_index, temperature_frame.shape)
    maximum_y, maximum_x = np.unravel_index(maximum_index, temperature_frame.shape)

    return TemperatureStatistics(
        center=float(temperature_frame[center_point[1], center_point[0]]),
        selected=float(temperature_frame[y, x]),
        minimum=float(temperature_frame[minimum_y, minimum_x]),
        maximum=float(temperature_frame[maximum_y, maximum_x]),
        average=float(np.mean(temperature_frame)),
        minimum_point=(int(minimum_x), int(minimum_y)),
        maximum_point=(int(maximum_x), int(maximum_y)),
    )


def expand_temperature_range(
    minimum: float,
    maximum: float,
    minimum_span: float,
) -> tuple[float, float]:
    if minimum_span <= 0:
        raise ValueError("최소 표시 온도 폭은 0보다 커야 합니다.")
    if maximum <= minimum:
        raise ValueError("최대 표시 온도는 최소 표시 온도보다 커야 합니다.")
    if maximum - minimum >= minimum_span:
        return float(minimum), float(maximum)

    center = (minimum + maximum) / 2.0
    half_span = minimum_span / 2.0
    return center - half_span, center + half_span


def apply_temperature_anchor(
    display_range: tuple[float, float],
    anchor_range: tuple[float, float],
) -> tuple[float, float]:
    return (
        min(display_range[0], anchor_range[0]),
        max(display_range[1], anchor_range[1]),
    )


def filter_display_temperature(
    current_frame: Any,
    previous_frame: Any | None,
    alpha: float,
    motion_threshold: float,
    np: Any,
) -> Any:
    if not 0.0 < alpha <= 1.0:
        raise ValueError("시간 필터 계수는 0보다 크고 1 이하여야 합니다.")
    if motion_threshold <= 0:
        raise ValueError("움직임 판단 온도 차이는 0보다 커야 합니다.")
    if previous_frame is None or previous_frame.shape != current_frame.shape:
        return current_frame.astype(np.float32, copy=True)

    difference = current_frame - previous_frame
    blended = previous_frame + difference * alpha
    return np.where(
        np.abs(difference) >= motion_threshold,
        current_frame,
        blended,
    ).astype(np.float32)


def calculate_marker_label_origin(
    marker: tuple[int, int],
    radius: int,
    text_size: tuple[int, int],
    image_size: tuple[int, int],
) -> tuple[int, int]:
    marker_x, marker_y = marker
    text_width, text_height = text_size
    image_width, image_height = image_size
    margin = 3

    x = marker_x + radius + 4
    baseline_y = marker_y - radius - 3
    if x + text_width > image_width - margin:
        x = marker_x - radius - text_width - 4
    if baseline_y - text_height < margin:
        baseline_y = marker_y + radius + text_height + 3

    maximum_x = max(0, image_width - text_width - margin)
    x = min(max(x, margin), maximum_x)
    baseline_y = min(
        max(baseline_y, text_height + margin),
        image_height - margin,
    )
    return x, baseline_y


def is_window_visible(cv2: Any, window_name: str) -> bool:
    try:
        return (
            cv2.getWindowProperty(
                window_name,
                cv2.WND_PROP_VISIBLE,
            )
            >= 1
        )
    except cv2.error:
        return False


class ThermalCapture:
    def __init__(
        self,
        device: str,
        width: int,
        height: int,
        backend: str,
        cv2: Any,
        np: Any,
    ) -> None:
        self.device = validate_device_path(device)
        self.width = width
        self.height = height
        self.backend = backend
        self.cv2 = cv2
        self.np = np
        self.capture: Any | None = None
        self.pending_frame: Any | None = None
        self.active_backend = ""

    def open(self) -> None:
        candidates = (
            [self.backend]
            if self.backend != "auto"
            else ["v4l2", "gstreamer"]
        )
        errors: list[str] = []

        for candidate in candidates:
            capture = self._open_backend(candidate)
            if not capture.isOpened():
                capture.release()
                errors.append(f"{candidate}: 장치를 열 수 없음")
                continue

            try:
                frame = self._read_first_frame(capture)
            except RuntimeError as exc:
                capture.release()
                errors.append(f"{candidate}: {exc}")
                continue

            self.capture = capture
            self.pending_frame = frame
            self.active_backend = candidate
            return

        detail = "\n".join(f"- {error}" for error in errors)
        raise RuntimeError(
            f"{self.device}에서 Y16 스트림을 열지 못했습니다.\n{detail}"
        )

    def read(self) -> Any:
        if self.pending_frame is not None:
            frame = self.pending_frame
            self.pending_frame = None
            return frame
        if self.capture is None:
            raise RuntimeError("카메라가 열리지 않았습니다.")

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            ok, frame = self.capture.read()
            if ok:
                return decode_y16_frame(
                    frame,
                    self.width,
                    self.height,
                    self.np,
                )
            time.sleep(0.01)
        raise RuntimeError("2초 동안 카메라 프레임을 받지 못했습니다.")

    def close(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def _open_backend(self, backend: str) -> Any:
        if backend == "v4l2":
            capture = self.cv2.VideoCapture(self.device, self.cv2.CAP_V4L2)
            capture.set(
                self.cv2.CAP_PROP_FOURCC,
                self.cv2.VideoWriter_fourcc("Y", "1", "6", " "),
            )
            capture.set(self.cv2.CAP_PROP_FRAME_WIDTH, self.width)
            capture.set(self.cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            capture.set(self.cv2.CAP_PROP_CONVERT_RGB, 0)
            capture.set(self.cv2.CAP_PROP_BUFFERSIZE, 1)
            return capture

        pipeline = (
            f"v4l2src device={self.device} ! "
            f"video/x-raw,format=GRAY16_LE,width={self.width},height={self.height} ! "
            "appsink drop=true max-buffers=1 sync=false"
        )
        return self.cv2.VideoCapture(pipeline, self.cv2.CAP_GSTREAMER)

    def _read_first_frame(self, capture: Any) -> Any:
        deadline = time.monotonic() + 4.0
        last_error: RuntimeError | None = None
        while time.monotonic() < deadline:
            ok, frame = capture.read()
            if not ok:
                time.sleep(0.01)
                continue
            try:
                return decode_y16_frame(frame, self.width, self.height, self.np)
            except RuntimeError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("4초 동안 프레임이 수신되지 않음")


class ThermalViewer:
    PALETTES = ["inferno", "turbo", "jet", "hot"]

    def __init__(
        self,
        capture: ThermalCapture,
        scale: int,
        palette: str,
        fixed_range: tuple[float, float] | None,
        anchor_range: tuple[float, float] | None,
        rotate: int,
        output_dir: Path,
        display_mode: str,
        cv2: Any,
        np: Any,
    ) -> None:
        self.capture = capture
        self.scale = scale
        self.palette = palette
        self.fixed_range = fixed_range
        self.anchor_range = anchor_range
        self.rotate = rotate
        self.output_dir = output_dir
        self.display_mode = display_mode
        self.cv2 = cv2
        self.np = np
        self.selected_point: tuple[int, int] | None = None
        self.window_name = "CDAS Thermal Camera"
        self.image_width = 0
        self.image_height = 0
        self.last_timestamp = time.monotonic()
        self.fps = 0.0
        self.filtered_display_frame: Any | None = None
        self.previous_display_range: tuple[float, float] | None = None

    def run(self) -> None:
        self.capture.open()
        print(
            f"{self.capture.device} 연결 완료 "
            f"(백엔드: {self.capture.active_backend})"
        )
        self.cv2.namedWindow(self.window_name, self.cv2.WINDOW_AUTOSIZE)
        self.cv2.setMouseCallback(self.window_name, self._handle_mouse)

        try:
            while True:
                raw_frame = rotate_frame(
                    self.capture.read(),
                    self.rotate,
                    self.np,
                )
                temperature_frame = raw_to_temperature(raw_frame, self.np)
                statistics = calculate_statistics(
                    temperature_frame,
                    self.selected_point,
                    self.np,
                )
                display, color_frame, display_range = self._render(
                    temperature_frame,
                    statistics,
                )
                self.cv2.imshow(self.window_name, display)

                key = self.cv2.waitKey(1) & 0xFF
                if not is_window_visible(self.cv2, self.window_name):
                    break
                if key in (27, ord("q")):
                    break
                if key == ord("s"):
                    self._save_snapshot(
                        raw_frame,
                        temperature_frame,
                        color_frame,
                    )
                elif key == ord("c"):
                    self.selected_point = None
                elif key == ord("p"):
                    self._cycle_palette()
                elif key == ord("d"):
                    self._toggle_display_mode()
                elif key == ord("a"):
                    self.fixed_range = None
                    self.previous_display_range = None

                self._update_fps()
                self._warn_if_tlinear_is_suspicious(statistics, display_range)
        finally:
            self.capture.close()
            self.cv2.destroyAllWindows()

    def _render(
        self,
        temperature_frame: Any,
        statistics: TemperatureStatistics,
    ) -> tuple[Any, Any, tuple[float, float]]:
        display_temperature_frame = self._prepare_display_frame(
            temperature_frame
        )
        display_range = self._resolve_display_range(display_temperature_frame)
        minimum, maximum = display_range
        normalized = self.np.clip(
            (display_temperature_frame - minimum) * 255.0
            / (maximum - minimum),
            0,
            255,
        ).astype(self.np.uint8)
        if self.display_mode == "smooth":
            normalized = self.cv2.bilateralFilter(
                normalized,
                d=5,
                sigmaColor=18,
                sigmaSpace=2,
            )
        color_frame = self.cv2.applyColorMap(
            normalized,
            self._palette_code(),
        )

        height, width = temperature_frame.shape
        self.image_width = width * self.scale
        self.image_height = height * self.scale
        enlarged = self.cv2.resize(
            color_frame,
            (self.image_width, self.image_height),
            interpolation=(
                self.cv2.INTER_CUBIC
                if self.display_mode == "smooth"
                else self.cv2.INTER_NEAREST
            ),
        )

        selected = self.selected_point or (width // 2, height // 2)
        self._draw_marker(
            enlarged,
            selected,
            (80, 255, 120),
            "P",
            statistics.selected,
        )
        self._draw_marker(
            enlarged,
            statistics.minimum_point,
            (255, 180, 40),
            "L",
            statistics.minimum,
        )
        self._draw_marker(
            enlarged,
            statistics.maximum_point,
            (40, 80, 255),
            "H",
            statistics.maximum,
        )

        panel_width = 260
        canvas_height = max(self.image_height, 480)
        canvas = self.np.full(
            (canvas_height, self.image_width + panel_width, 3),
            24,
            dtype=self.np.uint8,
        )
        canvas[: self.image_height, : self.image_width] = enlarged
        self._draw_status_panel(
            canvas,
            statistics,
            display_range,
            x=self.image_width + 22,
            panel_height=canvas_height,
        )
        return canvas, color_frame, display_range

    def _prepare_display_frame(self, temperature_frame: Any) -> Any:
        if self.display_mode == "raw":
            return temperature_frame

        filtered = filter_display_temperature(
            current_frame=temperature_frame,
            previous_frame=self.filtered_display_frame,
            alpha=TEMPORAL_FILTER_ALPHA,
            motion_threshold=MOTION_THRESHOLD_CELSIUS,
            np=self.np,
        )
        self.filtered_display_frame = filtered
        return filtered

    def _resolve_display_range(self, temperature_frame: Any) -> tuple[float, float]:
        if self.fixed_range is not None:
            return self.fixed_range
        minimum, maximum = self.np.percentile(temperature_frame, [2.0, 98.0])
        if maximum - minimum < 0.1:
            maximum = minimum + 0.1
        current_range = (float(minimum), float(maximum))
        if self.display_mode == "raw":
            return current_range

        current_range = expand_temperature_range(
            *current_range,
            minimum_span=MINIMUM_AUTO_DISPLAY_SPAN,
        )
        if self.anchor_range is not None:
            current_range = apply_temperature_anchor(
                current_range,
                self.anchor_range,
            )
        if self.previous_display_range is None:
            self.previous_display_range = current_range
            return current_range

        previous_minimum, previous_maximum = self.previous_display_range
        current_minimum, current_maximum = current_range
        range_alpha = 0.2
        smoothed_minimum = (
            current_minimum
            if current_minimum < previous_minimum
            else previous_minimum
            + (current_minimum - previous_minimum) * range_alpha
        )
        smoothed_maximum = (
            current_maximum
            if current_maximum > previous_maximum
            else previous_maximum
            + (current_maximum - previous_maximum) * range_alpha
        )
        self.previous_display_range = (
            smoothed_minimum,
            smoothed_maximum,
        )
        return self.previous_display_range

    def _draw_marker(
        self,
        image: Any,
        point: tuple[int, int],
        color: tuple[int, int, int],
        label: str,
        temperature: float,
    ) -> None:
        x = point[0] * self.scale + self.scale // 2
        y = point[1] * self.scale + self.scale // 2
        radius = max(4, self.scale + 1)
        self.cv2.circle(image, (x, y), radius, color, 1)
        self.cv2.line(image, (x - radius - 2, y), (x + radius + 2, y), color, 1)
        self.cv2.line(image, (x, y - radius - 2), (x, y + radius + 2), color, 1)
        text = f"{label} {temperature:.2f} C"
        font = self.cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.4
        thickness = 1
        (text_width, text_height), _baseline = self.cv2.getTextSize(
            text,
            font,
            font_scale,
            thickness,
        )
        text_origin = calculate_marker_label_origin(
            marker=(x, y),
            radius=radius,
            text_size=(text_width, text_height),
            image_size=(image.shape[1], image.shape[0]),
        )
        self.cv2.putText(
            image,
            text,
            text_origin,
            font,
            font_scale,
            (20, 20, 20),
            3,
            self.cv2.LINE_AA,
        )
        self.cv2.putText(
            image,
            text,
            text_origin,
            font,
            font_scale,
            color,
            thickness,
            self.cv2.LINE_AA,
        )

    def _draw_status_panel(
        self,
        canvas: Any,
        statistics: TemperatureStatistics,
        display_range: tuple[float, float],
        x: int,
        panel_height: int,
    ) -> None:
        lines = [
            ("POINT", statistics.selected, (80, 255, 120)),
            ("CENTER", statistics.center, (235, 235, 235)),
            ("MIN", statistics.minimum, (255, 180, 40)),
            ("MAX", statistics.maximum, (40, 80, 255)),
            ("AVERAGE", statistics.average, (235, 235, 235)),
        ]
        self.cv2.putText(
            canvas,
            "TEMPERATURE",
            (x, 42),
            self.cv2.FONT_HERSHEY_SIMPLEX,
            0.63,
            (245, 245, 245),
            1,
            self.cv2.LINE_AA,
        )

        y = 84
        for label, value, color in lines:
            self.cv2.putText(
                canvas,
                label,
                (x, y),
                self.cv2.FONT_HERSHEY_SIMPLEX,
                0.44,
                (155, 155, 155),
                1,
                self.cv2.LINE_AA,
            )
            self.cv2.putText(
                canvas,
                f"{value:7.2f} C",
                (x, y + 26),
                self.cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                color,
                1,
                self.cv2.LINE_AA,
            )
            y += 65

        self.cv2.putText(
            canvas,
            f"VIEW   {self.display_mode.upper()}",
            (x, panel_height - 80),
            self.cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (170, 170, 170),
            1,
            self.cv2.LINE_AA,
        )
        range_text = f"{display_range[0]:.1f} .. {display_range[1]:.1f} C"
        self.cv2.putText(
            canvas,
            f"RANGE  {range_text}",
            (x, panel_height - 54),
            self.cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (170, 170, 170),
            1,
            self.cv2.LINE_AA,
        )
        self.cv2.putText(
            canvas,
            f"FPS    {self.fps:4.1f}",
            (x, panel_height - 28),
            self.cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (170, 170, 170),
            1,
            self.cv2.LINE_AA,
        )

    def _handle_mouse(self, event: int, x: int, y: int, _flags: int, _param: Any) -> None:
        if event != self.cv2.EVENT_LBUTTONDOWN:
            return
        if 0 <= x < self.image_width and 0 <= y < self.image_height:
            self.selected_point = (x // self.scale, y // self.scale)

    def _palette_code(self) -> int:
        codes = {
            "inferno": self.cv2.COLORMAP_INFERNO,
            "turbo": self.cv2.COLORMAP_TURBO,
            "jet": self.cv2.COLORMAP_JET,
            "hot": self.cv2.COLORMAP_HOT,
        }
        return codes[self.palette]

    def _cycle_palette(self) -> None:
        index = self.PALETTES.index(self.palette)
        self.palette = self.PALETTES[(index + 1) % len(self.PALETTES)]
        print(f"팔레트 변경: {self.palette}")

    def _toggle_display_mode(self) -> None:
        self.display_mode = (
            "raw" if self.display_mode == "smooth" else "smooth"
        )
        self.filtered_display_frame = None
        self.previous_display_range = None
        print(f"화면 표시 방식 변경: {self.display_mode}")

    def _save_snapshot(
        self,
        raw_frame: Any,
        temperature_frame: Any,
        color_frame: Any,
    ) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        image_path = self.output_dir / f"{timestamp}_thermal.png"
        temperature_path = self.output_dir / f"{timestamp}_temperature.csv"
        raw_path = self.output_dir / f"{timestamp}_raw.npy"

        if not self.cv2.imwrite(str(image_path), color_frame):
            raise RuntimeError(f"이미지를 저장하지 못했습니다: {image_path}")
        self.np.savetxt(
            temperature_path,
            temperature_frame,
            delimiter=",",
            fmt="%.2f",
        )
        self.np.save(raw_path, raw_frame)
        print(f"열화상과 온도 데이터를 저장했습니다: {self.output_dir.resolve()}")

    def _update_fps(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_timestamp
        self.last_timestamp = now
        if elapsed <= 0:
            return
        current = 1.0 / elapsed
        self.fps = current if self.fps == 0.0 else self.fps * 0.9 + current * 0.1

    def _warn_if_tlinear_is_suspicious(
        self,
        statistics: TemperatureStatistics,
        display_range: tuple[float, float],
    ) -> None:
        if -80.0 <= statistics.average <= 600.0:
            return
        if getattr(self, "_tlinear_warning_shown", False):
            return
        self._tlinear_warning_shown = True
        print(
            "경고: 온도값이 일반적인 범위를 벗어났습니다. "
            "PureThermal의 Y16/TLinear 설정을 확인해 주세요.",
            file=sys.stderr,
        )
        print(
            f"현재 표시 범위: {display_range[0]:.2f}~{display_range[1]:.2f} C",
            file=sys.stderr,
        )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        device = validate_device_path(args.device)
        if (args.width is None) != (args.height is None):
            raise ValueError("--width와 --height는 함께 지정해야 합니다.")
        if args.width is None:
            width, height = detect_y16_resolution(device)
            print(f"Y16 해상도 감지: {width}x{height}")
        else:
            width, height = args.width, args.height
        if width <= 0 or height <= 0:
            raise ValueError("영상 크기는 1 이상이어야 합니다.")
        if not 1 <= args.scale <= 10:
            raise ValueError("--scale은 1에서 10 사이여야 합니다.")

        fixed_range = validate_temperature_range(args.min_temp, args.max_temp)
        anchor_range = validate_temperature_range(
            args.anchor_min_temp,
            args.anchor_max_temp,
            "--anchor-min-temp",
            "--anchor-max-temp",
        )
        cv2, np = _load_runtime_dependencies()
        capture = ThermalCapture(
            device=device,
            width=width,
            height=height,
            backend=args.backend,
            cv2=cv2,
            np=np,
        )
        viewer = ThermalViewer(
            capture=capture,
            scale=args.scale,
            palette=args.palette,
            fixed_range=fixed_range,
            anchor_range=anchor_range,
            rotate=args.rotate,
            output_dir=args.output_dir,
            display_mode=args.display_mode,
            cv2=cv2,
            np=np,
        )
        viewer.run()
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
