"""Shared, practical H.264 target-bitrate policy (kbps, not a wire-rate guarantee)."""

from typing import Optional

from bambucam.camera.models import Resolution
from bambucam.system_info import pi_capability_tier

RTSP_BITRATE_MIN_KBPS = 100
RTSP_BITRATE_MAX_KBPS = 20000
RTSP_BITRATE_CEILING_KBPS = {1: 3000, 2: 8000, 3: 20000}
RTSP_BITRATE_CEILING_FALLBACK_KBPS = 20000
RTSP_ENCODE_CEILING = {1: (640, 360), 2: (1280, 720), 3: (1920, 1080)}
RTSP_ENCODE_CEILING_FALLBACK = (1280, 720)


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
