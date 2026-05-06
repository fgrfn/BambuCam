"""System information helpers (CPU temp, memory, uptime, etc.)."""

import platform
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


def pi_capability_tier() -> int:
    """
    Return a hardware capability tier for adaptive defaults:
      1 — Pi Zero, Pi 1, Pi 2  → MJPEG-only, no lores stream
      2 — Pi 3                  → RTSP + MJPEG capped at 30 fps
      3 — Pi 4, Pi 5, non-Pi   → full stack, no caps
    """
    model = raspberry_pi_model()
    if model is None:
        return 3  # non-Pi hardware, assume capable
    m = model.lower()
    if any(x in m for x in ("pi zero", "pi 1 ", "pi 2 ")):
        return 1
    if any(x in m for x in ("pi 3 ", "pi 3b", "pi 3a")):
        return 2
    return 3


def system_summary() -> dict:
    return {
        "hostname": platform.node(),
        "pi_model": raspberry_pi_model(),
        "pi_tier": pi_capability_tier(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "cpu_temp_c": cpu_temperature(),
        "cpu_usage_pct": cpu_usage(),
        "memory": memory_info(),
        "disk": disk_info(),
        "uptime_seconds": uptime_seconds(),
    }
