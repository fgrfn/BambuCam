"""Shared, practical H.264 target-bitrate policy (kbps, not a wire-rate guarantee)."""

from math import ceil
from typing import Optional

from bambucam.camera.models import Resolution
from bambucam.system_info import pi_capability_tier

RTSP_BITRATE_MIN_KBPS = 100
RTSP_BITRATE_MAX_KBPS = 20000
RTSP_BITRATE_CEILING_KBPS = {1: 3000, 2: 8000, 3: 20000}
RTSP_BITRATE_CEILING_FALLBACK_KBPS = 20000
RTSP_ENCODE_CEILING = {1: (640, 360), 2: (1280, 720), 3: (1920, 1080)}
RTSP_ENCODE_CEILING_FALLBACK = (1280, 720)


# The VideoCore H.264 encoder (Pi 4/CM4 and older) is specified for 1080p30, the
# macroblock rate of H.264 level 4.0. Faster streams make it drop frames.
HW_H264_MAX_MACROBLOCKS_PER_SECOND = 245760


def hardware_h264_fps_limit(size: tuple[int, int]) -> int:
    macroblocks = ceil(size[0] / 16) * ceil(size[1] / 16)
    return max(1, HW_H264_MAX_MACROBLOCKS_PER_SECOND // macroblocks)


def limit_to_hardware_h264(video_modes: list[dict], tier: int) -> list[dict]:
    """Cap each video mode's frame rate at what the hardware encoder sustains."""
    result = []
    for item in video_modes:
        size = encode_size(Resolution.from_string(item["resolution"]), tier)
        limit = hardware_h264_fps_limit(size)
        if int(item["max_fps"]) > limit:
            item = {**item, "max_fps": limit, "limited_by": "h264_encoder"}
        result.append(item)
    return result


def encode_size(resolution: Resolution, tier: int) -> tuple[int, int]:
    """Fit the main stream into the hardware tier's existing encode ceiling."""
    ceiling = RTSP_ENCODE_CEILING.get(tier, RTSP_ENCODE_CEILING_FALLBACK)
    scale = min(1.0, ceiling[0] / resolution.width, ceiling[1] / resolution.height)
    return max(32, int(resolution.width * scale) & ~1), max(32, int(resolution.height * scale) & ~1)


def bitrate_limits(resolution=None, tier: Optional[int] = None) -> dict:
    """Return the lower of hardware and encoded-pixel-area ceilings.

    An unresolved (auto) resolution defers the area limit until runtime knows
    the actual encode stream. These are application policy, not codec maxima.
    """
    tier = pi_capability_tier() if tier is None else tier
    hardware_max = RTSP_BITRATE_CEILING_KBPS.get(tier, RTSP_BITRATE_CEILING_FALLBACK_KBPS)
    area_max = RTSP_BITRATE_MAX_KBPS
    size = None
    if resolution is not None and str(resolution).lower() != "auto":
        if isinstance(resolution, (tuple, list)):
            resolution = Resolution(*resolution)
        elif not isinstance(resolution, Resolution):
            resolution = Resolution.from_string(str(resolution))
        if resolution.width < 1 or resolution.height < 1:
            raise ValueError("RTSP resolution dimensions must be positive")
        size = str(resolution)
        area = resolution.width * resolution.height
        for pixels, maximum in ((640 * 480, 2000), (1280 * 720, 6000), (1920 * 1080, 12000)):
            if area <= pixels:
                area_max = maximum
                break
    return {
        "min_kbps": RTSP_BITRATE_MIN_KBPS,
        "max_kbps": min(hardware_max, area_max),
        "hardware_max_kbps": hardware_max,
        "resolution_max_kbps": area_max,
        "hardware_tier": tier,
        "resolution": size,
    }


def validate_bitrate(value, limits: dict) -> int:
    if type(value) is not int:
        raise ValueError("RTSP bitrate_kbps must be an integer")
    minimum, maximum = limits["min_kbps"], limits["max_kbps"]
    if not minimum <= value <= maximum:
        raise ValueError(
            f"RTSP bitrate must be between {minimum} and {maximum} kbps "
            f"for {limits['resolution'] or 'auto resolution'} "
            f"on hardware tier {limits['hardware_tier']}"
        )
    return value
