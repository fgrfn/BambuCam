"""Native CSI sensor modes and their scaled ISP outputs.

Never infer modes from a camera header or its crop rectangle. A scaled output
uses the smallest covering native mode; that same mode is pinned in Picamera2.
"""

from dataclasses import dataclass
from math import floor
from typing import Optional

from bambucam.camera.models import Resolution


@dataclass(frozen=True)
class SensorMode:
    resolution: Resolution
    max_fps: float
    format: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "resolution": str(self.resolution),
            "max_fps": self.max_fps,
            "format": self.format,
        }


# Verified on OV5647/CM4 with the camera stopped. Used only when enumeration
# is unavailable or contaminated by an already configured camera.
OV5647_FALLBACK_MODES = [
    SensorMode(Resolution(2592, 1944), 15.63, "SGBRG10_CSI2P"),
    SensorMode(Resolution(1920, 1080), 32.81, "SGBRG10_CSI2P"),
    SensorMode(Resolution(1296, 972), 46.34, "SGBRG10_CSI2P"),
    SensorMode(Resolution(640, 480), 62.5, "SGBRG10_CSI2P"),
]


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
