"""Native CSI sensor modes and their scaled ISP outputs.

Never infer modes from a camera header or its crop rectangle. A scaled output
uses the smallest covering native mode; that same mode is pinned in Picamera2.
"""

from dataclasses import dataclass
from math import floor, gcd
from typing import Optional

from bambucam.camera.models import Resolution


@dataclass(frozen=True)
class SensorMode:
    resolution: Resolution
    max_fps: float
    format: Optional[str] = None
    # Sensor area read out for this mode as (x, y, width, height) in full-frame
    # pixels, as reported by rpicam --list-cameras. None when unknown (V4L2).
    crop: Optional[tuple[int, int, int, int]] = None

    def as_dict(self) -> dict:
        return {
            "resolution": str(self.resolution),
            "max_fps": self.max_fps,
            "format": self.format,
            "crop": list(self.crop) if self.crop else None,
        }


# Verified on OV5647/CM4 with the camera stopped. Used only when enumeration
# is unavailable or contaminated by an already configured camera.
OV5647_FALLBACK_MODES = [
    SensorMode(Resolution(2592, 1944), 15.63, "SGBRG10_CSI2P", (0, 0, 2592, 1944)),
    SensorMode(Resolution(1920, 1080), 32.81, "SGBRG10_CSI2P", (348, 434, 1928, 1080)),
    SensorMode(Resolution(1296, 972), 46.34, "SGBRG10_CSI2P", (0, 0, 2592, 1944)),
    SensorMode(Resolution(640, 480), 62.5, "SGBRG10_CSI2P", (16, 0, 2560, 1920)),
]

_COMMON_ASPECTS = [(16, 9), (4, 3), (3, 2), (16, 10), (5, 4), (1, 1)]


def aspect_label(resolution: Resolution) -> str:
    """Name the aspect ratio, tolerating sensor sizes such as 3280x2464 (~4:3)."""
    ratio = resolution.width / resolution.height
    for width, height in _COMMON_ASPECTS:
        if abs(ratio / (width / height) - 1) <= 0.02:
            return f"{width}:{height}"
    divisor = gcd(resolution.width, resolution.height)
    return f"{resolution.width // divisor}:{resolution.height // divisor}"


def field_of_view_width_pct(
    modes: list[SensorMode], mode: Optional[SensorMode], output: Resolution
) -> Optional[int]:
    """Share of the full sensor width visible in an output, or None if unknown.

    Native modes may read out only a window of the sensor (OV5647 1920x1080 is a
    centre crop). The Raspberry Pi ISP then crops the mode further to the output's
    aspect ratio instead of stretching, which narrows the width for taller outputs.
    """
    if mode is None or mode.crop is None:
        return None
    full_width = max((m.crop[0] + m.crop[2] for m in modes if m.crop), default=0)
    if full_width <= 0:
        return None
    crop_width, crop_height = mode.crop[2], mode.crop[3]
    visible_width = min(crop_width, crop_height * output.width / output.height)
    return min(100, round(100 * visible_width / full_width))


def select_sensor_mode(modes: list[SensorMode], output: Resolution) -> Optional[SensorMode]:
    covering = [
        mode
        for mode in modes
        if mode.resolution.width >= output.width and mode.resolution.height >= output.height
    ]
    return min(
        covering,
        key=lambda mode: (
            mode.resolution.width * mode.resolution.height,
            -mode.max_fps,
        ),
        default=None,
    )


def output_capabilities(model, resolutions, modes, source) -> list[dict]:
    outputs = []
    for resolution in resolutions:
        mode = select_sensor_mode(modes, resolution)
        if modes and mode is None:
            continue  # Do not advertise upscaling beyond the sensor.
        maximum = (
            mode.max_fps
            if mode
            else model.resolution_max_framerates.get(resolution, model.max_framerate)
        )
        outputs.append(
            {
                "resolution": str(resolution),
                "kind": (
                    "native"
                    if mode and mode.resolution == resolution
                    else "scaled" if mode else "model"
                ),
                "sensor_resolution": str(mode.resolution) if mode else None,
                "sensor_max_fps": maximum if mode else None,
                "max_fps": min(120, floor(maximum)),
                "source": source,
                "aspect": aspect_label(resolution),
                "fov_width_pct": field_of_view_width_pct(modes, mode, resolution),
            }
        )
    return outputs


def video_output_capabilities(model, output_modes: list[dict]) -> list[dict]:
    """Restrict video choices without discarding high-resolution still/sensor modes.

    Native CSI modes also include full-frame still capture resolutions. A
    camera's advertised video specification can be narrower than its sensor
    inventory, so the stream form must not use output_modes unfiltered.
    """
    result = []
    for item in output_modes:
        resolution = Resolution.from_string(item["resolution"])
        cap = model.video_max_resolution
        if cap is not None and (resolution.width > cap.width or resolution.height > cap.height):
            continue
        fps = min(
            int(item["max_fps"]),
            int(model.video_max_framerates.get(resolution, item["max_fps"])),
        )
        result.append({**item, "max_fps": fps})
    return result
