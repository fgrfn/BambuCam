"""Standalone post-update health guard with package rollback.

This module is executed from the pre-update backup, so it remains usable even
when the newly installed BambuCam package cannot be imported.
"""

import json
import os
import shutil
import signal
import ssl
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def _healthy(url: str, expected_version: str) -> bool:
    try:
        context = ssl._create_unverified_context() if url.startswith("https://") else None
        try:
            response = urlopen(url, timeout=2, context=context)
        except HTTPError as exc:
            # A degraded camera stack returns 503 but still proves that the new
            # application started and can serve a versioned health payload.
            response = exc
        with response:
            payload = json.loads(response.read(64 * 1024).decode("utf-8"))
        return str(payload.get("version", "")) == expected_version
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False


def _restore(site_packages: Path, backup: Path) -> None:
    package_dir = site_packages / "bambucam"
    if package_dir.exists():
        shutil.rmtree(package_dir)
    for metadata_dir in site_packages.glob("bambucam-*.dist-info"):
        shutil.rmtree(metadata_dir)
    shutil.copytree(backup / "bambucam", package_dir)
    for metadata_dir in backup.glob("bambucam-*.dist-info"):
        shutil.copytree(metadata_dir, site_packages / metadata_dir.name)


def _restart_previous_process(python: Path, parent_pid: int, argv: list[str]) -> None:
    try:
        os.kill(parent_pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except OSError:
        pass
    if os.environ.get("INVOCATION_ID"):
        # systemd's Restart=on-failure restarts the restored package.
        return
    time.sleep(1)
    command = [str(python), "-m", "bambucam.main", *argv[1:]]
    subprocess.Popen(command, start_new_session=True, close_fds=True)


def main() -> int:
    if len(sys.argv) != 9:
        return 2
    health_url = sys.argv[1]
    expected_version = sys.argv[2]
    site_packages = Path(sys.argv[3])
    backup = Path(sys.argv[4])
    parent_pid = int(sys.argv[5])
    temp_root = Path(sys.argv[6])
    python = Path(sys.argv[7])
    argv = json.loads(sys.argv[8])

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if _healthy(health_url, expected_version):
            shutil.rmtree(temp_root, ignore_errors=True)
            return 0
        time.sleep(1)

    _restore(site_packages, backup)
    _restart_previous_process(python, parent_pid, argv)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
