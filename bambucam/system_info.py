"""System information helpers (CPU temp, memory, uptime, etc.)."""

import platform
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

_BOOT_TIME = time.time()


def cpu_temperature() -> Optional[float]:
    """Return CPU temperature in °C or None if unavailable."""
    # Raspberry Pi OS path
    for path in [
        "/sys/class/thermal/thermal_zone0/temp",
        "/sys/devices/virtual/thermal/thermal_zone0/temp",
    ]:
        try:
            return int(Path(path).read_text().strip()) / 1000.0
        except (OSError, ValueError):
            pass
    return None


_THROTTLED_PATH = Path("/sys/devices/platform/soc/soc:firmware/get_throttled")
# Firmware throttle flags (same word as `vcgencmd get_throttled`). Bits 0-3 are
# the current state; the same flags shifted by 16 latch until the next boot.
_THROTTLE_FLAGS = {
    0: "under_voltage",
    1: "freq_capped",
    2: "throttled",
    3: "soft_temp_limit",
}


def _read_throttled() -> Optional[int]:
    try:
        return int(_THROTTLED_PATH.read_text().strip(), 16)
    except (OSError, ValueError):
        pass
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"throttled=(0x[0-9a-fA-F]+)", result.stdout)
    return int(match.group(1), 16) if match else None


def throttle_status() -> Optional[dict]:
    """Report undervoltage and thermal throttling, or None off Raspberry Pi firmware.

    A CM4 next to a printer runs warm, and carrier boards are often fed by
    marginal supplies; both show up as dropped frames long before anything fails.
    """
    raw = _read_throttled()
    if raw is None:
        return None
    return {
        "raw": f"0x{raw:x}",
        "active": [name for bit, name in _THROTTLE_FLAGS.items() if raw & (1 << bit)],
        "since_boot": [name for bit, name in _THROTTLE_FLAGS.items() if raw & (1 << (bit + 16))],
    }


def memory_info() -> dict:
    try:
        import psutil

        mem = psutil.virtual_memory()
        return {
            "total_mb": mem.total // (1024 * 1024),
            "available_mb": mem.available // (1024 * 1024),
            "used_mb": mem.used // (1024 * 1024),
            "percent": mem.percent,
        }
    except ImportError:
        return {}


def cpu_usage() -> float:
    try:
        import psutil

        return psutil.cpu_percent(interval=0.2)
    except ImportError:
        return 0.0


def disk_info(path: str = "/") -> dict:
    try:
        import psutil

        disk = psutil.disk_usage(path)
        return {
            "total_gb": round(disk.total / (1024**3), 1),
            "used_gb": round(disk.used / (1024**3), 1),
            "free_gb": round(disk.free / (1024**3), 1),
            "percent": disk.percent,
        }
    except ImportError:
        return {}


def uptime_seconds() -> float:
    # /proc/uptime is properly namespaced in LXC containers; psutil.boot_time()
    # reads /proc/stat which reflects the host boot time inside LXC.
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        pass
    try:
        import psutil

        return time.time() - psutil.boot_time()
    except ImportError:
        return time.time() - _BOOT_TIME


def raspberry_pi_model() -> Optional[str]:
    """Read the Raspberry Pi model from /proc/device-tree/model."""
    try:
        return Path("/proc/device-tree/model").read_text().rstrip("\x00")
    except OSError:
        return None


def _compute_module_tier(model: str) -> Optional[int]:
    """Map a Compute Module model string to its tier, or None for other boards."""
    if "compute module" not in model:
        return None
    generation = re.search(r"compute module (\d+)", model)
    if generation is None:
        # CM1 is the only module without a number: "Raspberry Pi Compute Module Rev 1.1".
        return 1
    number = int(generation.group(1))
    if number <= 1:
        return 1  # CM1 — BCM2835, Pi 1 class
    if number <= 3:
        return 2  # CM3 / CM3+ — BCM2837, Pi 3 class
    return 3  # CM4 / CM4S / CM5 and newer — BCM2711 and up, Pi 4 class or better


def pi_capability_tier() -> int:
    """
    Return a hardware capability tier for adaptive defaults:
      1 — Pi Zero (orig), Pi 1, Pi 2, CM1        → MJPEG-only, no lores stream
      2 — Pi Zero 2 W, Pi 3, CM3/CM3+            → RTSP + MJPEG
      3 — Pi 4, Pi 5, CM4/CM4S/CM5, non-Pi       → full stack

    Tiers select streaming defaults only and never cap a configured frame rate.
    """
    model = raspberry_pi_model()
    if model is None:
        return 3  # non-Pi hardware, assume capable
    m = model.lower()
    # Compute Modules name no "Pi <n>", so they are classified by module generation.
    module_tier = _compute_module_tier(m)
    if module_tier is not None:
        return module_tier
    # Zero 2 W has 4× Cortex-A53 cores (same die as Pi 3) — check before "pi zero"
    if any(x in m for x in ("pi zero 2", "pi 3 ", "pi 3b", "pi 3a")):
        return 2
    if any(x in m for x in ("pi zero", "pi 1 ", "pi 2 ")):
        return 1
    return 3


def compute_module_camera_hint() -> Optional[dict]:
    """Return where CSI camera overlays belong on a Compute Module, else None.

    Carrier boards for Compute Modules wire the CSI connectors differently, and
    camera auto-detection frequently finds nothing there; the sensor overlay then
    has to be named explicitly, with ",cam0" for a camera on the CAM0 port.
    """
    model = raspberry_pi_model()
    if not model or "compute module" not in model.lower():
        return None
    firmware_config = Path("/boot/firmware/config.txt")
    config_file = firmware_config if firmware_config.exists() else Path("/boot/config.txt")
    return {
        "board": model,
        "config_file": str(config_file),
        "lines": ["camera_auto_detect=0", "dtoverlay=<sensor>"],
        "cam0_line": "dtoverlay=<sensor>,cam0",
        "sensors": {"ov5647": "v1", "imx219": "v2", "imx708": "v3", "imx477": "HQ"},
    }


def compute_module_camera_hint_text(hint: dict) -> str:
    sensors = ", ".join(f"{name} = {label}" for name, label in hint["sensors"].items())
    return (
        f"{hint['board']}: add to {hint['config_file']} and reboot: "
        f"{'; '.join(hint['lines'])} (use {hint['cam0_line']} for the CAM0 port; "
        f"sensor: {sensors})"
    )


def hardware_recommendations(tier: Optional[int] = None) -> dict:
    """Return conservative fresh-install defaults for the detected hardware tier."""
    resolved_tier = pi_capability_tier() if tier is None else int(tier)
    return {
        "rtsp_enabled": resolved_tier >= 2,
        "recommended_profile": "low_power" if resolved_tier == 1 else "balanced",
    }


def system_summary() -> dict:
    from bambucam.system_control import system_reboot_available

    tier = pi_capability_tier()
    return {
        "hostname": platform.node(),
        "pi_model": raspberry_pi_model(),
        "pi_tier": tier,
        "hardware_recommendations": hardware_recommendations(tier),
        "os": platform.platform(),
        "python": platform.python_version(),
        "cpu_temp_c": cpu_temperature(),
        "throttling": throttle_status(),
        "cpu_usage_pct": cpu_usage(),
        "memory": memory_info(),
        "disk": disk_info(),
        "uptime_seconds": uptime_seconds(),
        "can_reboot": system_reboot_available(),
    }
