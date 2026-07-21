"""Narrow host-level controls used by the WebUI."""

import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

_reboot_lock = threading.Lock()
_reboot_pending = False


def _systemctl_path() -> Optional[str]:
    if platform.system() != "Linux":
        return None
    return shutil.which("systemctl")


def system_reboot_available() -> bool:
    """Return whether systemd and the narrow Polkit authorization are available."""
    if _systemctl_path() is None:
        return False
    pkcheck = shutil.which("pkcheck")
    if pkcheck is None:
        return False
    try:
        result = subprocess.run(
            [
                pkcheck,
                "--action-id",
                "org.freedesktop.login1.reboot",
                "--process",
                str(os.getpid()),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def schedule_system_reboot() -> bool:
    """Schedule a host reboot after the current HTTP response has been sent."""
    global _reboot_pending

    systemctl = _systemctl_path()
    if systemctl is None:
        raise RuntimeError("System reboot is only available on Linux systems using systemd")
    if not system_reboot_available():
        raise RuntimeError(
            "System reboot permission is not installed; run the BambuCam installer once"
        )

    with _reboot_lock:
        if _reboot_pending:
            return False
        _reboot_pending = True

    try:
        threading.Thread(
            target=_delayed_system_reboot,
            args=(systemctl,),
            daemon=True,
            name="bambucam-system-reboot",
        ).start()
    except Exception:
        with _reboot_lock:
            _reboot_pending = False
        raise
    return True


def _delayed_system_reboot(systemctl: str) -> None:
    global _reboot_pending

    time.sleep(1.0)
    try:
        log.warning("Raspberry Pi reboot requested through the WebUI")
        subprocess.run(
            [systemctl, "--no-wall", "reboot"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        log.exception("Requested system reboot failed")
    finally:
        # Normally the host shuts down first. Reset the guard if systemctl fails
        # or returns without rebooting so a later retry remains possible.
        with _reboot_lock:
            _reboot_pending = False
