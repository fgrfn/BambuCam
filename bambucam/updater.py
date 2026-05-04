"""
BambuCam update service.

Update flow:
  1. check()       — query GitHub Releases API for latest version
  2. download()    — download the source tarball to a temp directory
  3. install()     — run pip install inside the venv
  4. restart()     — restart the systemd service (or the process)

All operations are non-blocking: a background thread runs the install and
posts progress events that the WebUI can poll via GET /api/v1/update/progress.
"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
GITHUB_REPO = "fgrfn/bambucam"
VENV_PIP = Path("/opt/bambucam/venv/bin/pip")
INSTALL_SCRIPT = Path("/opt/bambucam/venv/lib").glob(
    "python*/site-packages/bambucam/../../../../../../scripts/install.sh"
)


class UpdateState(str, Enum):
    IDLE = "idle"
    CHECKING = "checking"
    AVAILABLE = "available"
    UP_TO_DATE = "up_to_date"
    DOWNLOADING = "downloading"
    INSTALLING = "installing"
    RESTARTING = "restarting"
    SUCCESS = "success"
    ERROR = "error"


@dataclass
class ReleaseInfo:
    version: str
    tag: str
    name: str
    body: str
    published_at: str
    tarball_url: str
    html_url: str
    is_prerelease: bool
    assets: list = field(default_factory=list)


@dataclass
class UpdateStatus:
    state: UpdateState = UpdateState.IDLE
    current_version: str = ""
    latest_version: str = ""
    latest_release: Optional[ReleaseInfo] = None
    progress: int = 0  # 0-100
    message: str = ""
    error: str = ""
    update_available: bool = False
    checked_at: Optional[float] = None

    def as_dict(self) -> dict:
        d = {
            "state": self.state.value,
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "update_available": self.update_available,
            "checked_at": self.checked_at,
        }
        if self.latest_release:
            d["latest_release"] = {
                "version": self.latest_release.version,
                "tag": self.latest_release.tag,
                "name": self.latest_release.name,
                "body": self.latest_release.body,
                "published_at": self.latest_release.published_at,
                "html_url": self.latest_release.html_url,
                "is_prerelease": self.latest_release.is_prerelease,
            }
        return d


class Updater:
    """
    Manages version checking and in-place upgrades from GitHub Releases.

    Thread-safety: all public methods can be called from any thread.
    The install/restart flow runs in a dedicated background thread.
    """

    def __init__(
        self,
        current_version: str,
        repo: str = GITHUB_REPO,
        include_prerelease: bool = False,
        pip_path: Path = VENV_PIP,
    ):
        self._current = current_version
        self._repo = repo
        self._include_prerelease = include_prerelease
        self._pip_path = pip_path
        self._status = UpdateStatus(current_version=current_version)
        self._lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None

    # ---------------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------------

    def check(self) -> UpdateStatus:
        """
        Synchronously check GitHub for the latest release.
        Returns the updated status.
        """
        with self._lock:
            if self._status.state in (UpdateState.DOWNLOADING, UpdateState.INSTALLING):
                return self._status
            self._status.state = UpdateState.CHECKING
            self._status.message = "Prüfe auf Updates…"
            self._status.error = ""

        try:
            release = self._fetch_latest_release()
        except Exception as e:
            self._set_error(f"Verbindung zu GitHub fehlgeschlagen: {e}")
            return self._status

        with self._lock:
            self._status.latest_release = release
            self._status.latest_version = release.version
            self._status.checked_at = time.time()

            if _version_gt(release.version, self._current):
                self._status.state = UpdateState.AVAILABLE
                self._status.update_available = True
                self._status.message = f"Update verfügbar: v{release.version}"
            else:
                self._status.state = UpdateState.UP_TO_DATE
                self._status.update_available = False
                self._status.message = "BambuCam ist aktuell."

        return self._status

    def start_update(self) -> bool:
        """
        Start the download → install → restart pipeline in a background thread.
        Returns False if an update is already running or no update is available.
        """
        with self._lock:
            if self._status.state in (
                UpdateState.DOWNLOADING,
                UpdateState.INSTALLING,
                UpdateState.RESTARTING,
            ):
                log.warning("Update already in progress")
                return False
            if not self._status.update_available or self._status.latest_release is None:
                log.warning("No update available — call check() first")
                return False

        self._worker = threading.Thread(
            target=self._update_pipeline,
            daemon=True,
            name="bambucam-updater",
        )
        self._worker.start()
        return True

    @property
    def status(self) -> UpdateStatus:
        with self._lock:
            return self._status

    # ---------------------------------------------------------------------------
    # Internal pipeline
    # ---------------------------------------------------------------------------

    def _update_pipeline(self) -> None:
        try:
            release = self._status.latest_release
            assert release is not None

            # 1. Download
            self._set_state(UpdateState.DOWNLOADING, "Lade Update herunter…", 5)
            tarball_path = self._download(release)

            # 2. Install
            self._set_state(UpdateState.INSTALLING, "Installiere Update…", 50)
            self._install(tarball_path)

            # 3. Cleanup
            try:
                shutil.rmtree(tarball_path.parent, ignore_errors=True)
            except Exception:
                pass

            # 4. Restart
            self._set_state(UpdateState.RESTARTING, "Starte BambuCam neu…", 90)
            self._restart()

            with self._lock:
                self._status.state = UpdateState.SUCCESS
                self._status.current_version = release.version
                self._status.update_available = False
                self._status.progress = 100
                self._status.message = (
                    f"Update auf v{release.version} erfolgreich! " "BambuCam wird neu gestartet…"
                )

        except Exception as e:
            log.exception("Update pipeline failed")
            self._set_error(str(e))

    def _download(self, release: ReleaseInfo) -> Path:
        """Download the wheel (preferred) or source tarball; return path to local file."""
        tmp_dir = Path(tempfile.mkdtemp(prefix="bambucam_update_"))

        # Prefer pre-built wheel: version metadata is injected by the release
        # workflow, so the installed package will report the correct version.
        wheel_url = next(
            (a["url"] for a in release.assets if a["name"].endswith(".whl")),
            None,
        )
        if wheel_url:
            local_path = tmp_dir / f"bambucam-{release.version}-py3-none-any.whl"
            download_url = wheel_url
            # GitHub release asset: must request as octet-stream
            headers = {"Accept": "application/octet-stream"}
            log.info("Downloading wheel %s → %s", download_url, local_path)
        else:
            local_path = tmp_dir / f"bambucam-{release.version}.tar.gz"
            download_url = release.tarball_url
            # GitHub tarball API endpoint: no Accept override (octet-stream → 415)
            headers = {}
            log.info("Wheel not found in assets, falling back to tarball %s", download_url)

        response = requests.get(download_url, headers=headers, stream=True, timeout=60)
        response.raise_for_status()

        total = int(response.headers.get("content-length", 0))
        downloaded = 0

        with local_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = 5 + int(40 * downloaded / total)
                    self._set_progress(pct, f"Lade herunter… {downloaded // 1024} KB")

        log.info("Download complete: %s (%d bytes)", local_path, downloaded)
        return local_path

    def _install(self, package_path: Path) -> None:
        """Install the downloaded wheel or tarball into the venv."""
        pip = self._pip_path if self._pip_path.exists() else Path(sys.executable).parent / "pip"

        install_target = package_path

        # GitHub source tarballs wrap everything in a top-level directory
        # (e.g. fgrfn-BambuCam-abc123/), so pip can't find pyproject.toml at
        # the archive root.  Extract and point pip at the inner directory.
        if package_path.name.endswith(".tar.gz"):
            import tarfile

            extract_dir = package_path.parent / "src"
            extract_dir.mkdir(exist_ok=True)
            with tarfile.open(package_path) as tf:
                tf.extractall(extract_dir)
            subdirs = [p for p in extract_dir.iterdir() if p.is_dir()]
            if subdirs:
                install_target = subdirs[0]
                log.info("Tarball extracted to %s", install_target)

        # --no-user: never fall back to user-site install (venv pip only)
        # HOME=/tmp: service user has no home dir; avoids pip cache permission warning
        cmd = [str(pip), "install", "--upgrade", "--no-user", str(install_target)]
        env = os.environ.copy()
        env["HOME"] = "/tmp"
        env["PIP_NO_CACHE_DIR"] = "1"

        log.info("Running: %s", " ".join(cmd))
        self._set_progress(55, "Installiere Python-Paket…")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
        if result.returncode != 0:
            raise RuntimeError(f"pip install failed (exit {result.returncode}):\n{result.stderr}")
        log.info("pip install succeeded:\n%s", result.stdout)
        self._set_progress(85, "Installation abgeschlossen.")

    def _restart(self) -> None:
        """Restart the BambuCam service via systemctl or by re-execing."""
        self._set_progress(92, "Neustart wird eingeleitet…")

        # Try systemd first (production)
        if shutil.which("systemctl"):
            result = subprocess.run(
                ["systemctl", "restart", "bambucam"],
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                log.info("systemd restart triggered")
                return

        # Fallback: re-exec the current process
        log.warning("systemctl not available — re-execing process")
        time.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # ---------------------------------------------------------------------------
    # GitHub API
    # ---------------------------------------------------------------------------

    def _fetch_latest_release(self) -> ReleaseInfo:
        url = f"{GITHUB_API}/repos/{self._repo}/releases"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        releases = response.json()

        if not releases:
            raise RuntimeError("No releases found on GitHub")

        for rel in releases:
            if rel.get("draft"):
                continue
            if rel.get("prerelease") and not self._include_prerelease:
                continue

            tag = rel["tag_name"]
            version = tag.lstrip("v")

            return ReleaseInfo(
                version=version,
                tag=tag,
                name=rel.get("name") or tag,
                body=rel.get("body") or "",
                published_at=rel.get("published_at", ""),
                tarball_url=rel["tarball_url"],
                html_url=rel["html_url"],
                is_prerelease=rel.get("prerelease", False),
                assets=[
                    {
                        "name": a["name"],
                        "url": a["browser_download_url"],
                        "size": a["size"],
                    }
                    for a in rel.get("assets", [])
                ],
            )

        raise RuntimeError("No suitable release found")

    # ---------------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------------

    def _set_state(self, state: UpdateState, message: str, progress: int) -> None:
        with self._lock:
            self._status.state = state
            self._status.message = message
            self._status.progress = progress
        log.info("[updater] %s — %s", state.value, message)

    def _set_progress(self, progress: int, message: str = "") -> None:
        with self._lock:
            self._status.progress = progress
            if message:
                self._status.message = message

    def _set_error(self, error: str) -> None:
        with self._lock:
            self._status.state = UpdateState.ERROR
            self._status.error = error
            self._status.message = f"Fehler: {error}"
            self._status.progress = 0
        log.error("[updater] %s", error)


# ---------------------------------------------------------------------------
# Semantic version comparison (no external dependencies)
# ---------------------------------------------------------------------------


def _parse_version(v: str) -> tuple:
    """Parse 'x.y.z' or 'x.y.z-suffix' into comparable tuple."""
    v = v.lstrip("v").split("-")[0]
    try:
        return tuple(int(p) for p in v.split("."))
    except ValueError:
        return (0,)


def _version_gt(a: str, b: str) -> bool:
    """Return True if version a is strictly greater than b."""
    return _parse_version(a) > _parse_version(b)
