"""Secure, thread-safe in-place updater for BambuCam releases."""

import copy
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tarfile
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
MAX_PACKAGE_BYTES = 100 * 1024 * 1024
MAX_CHECKSUM_BYTES = 1024 * 1024
_ACTIVE_STATES = {
    "downloading",
    "installing",
    "restarting",
}


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
    progress: int = 0
    message: str = ""
    error: str = ""
    update_available: bool = False
    checked_at: Optional[float] = None

    def as_dict(self) -> dict:
        result = {
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
            result["latest_release"] = {
                "version": self.latest_release.version,
                "tag": self.latest_release.tag,
                "name": self.latest_release.name,
                "body": self.latest_release.body,
                "published_at": self.latest_release.published_at,
                "html_url": self.latest_release.html_url,
                "is_prerelease": self.latest_release.is_prerelease,
            }
        return result


class Updater:
    """Check, verify, install, and activate BambuCam GitHub releases."""

    _AUTO_CHECK_INITIAL_DELAY = 60
    _AUTO_CHECK_INTERVAL = 24 * 3600
    _RELEASES_CACHE_TTL = 3600

    def __init__(
        self,
        current_version: str,
        repo: str = GITHUB_REPO,
        include_prerelease: bool = False,
        pip_path: Path = VENV_PIP,
        auto_check: bool = True,
        max_package_bytes: int = MAX_PACKAGE_BYTES,
    ):
        self._current = current_version
        self._repo = repo
        self._include_prerelease = include_prerelease
        self._pip_path = Path(pip_path)
        self._max_package_bytes = int(max_package_bytes)
        self._status = UpdateStatus(current_version=current_version)
        self._lock = threading.RLock()
        self._worker: Optional[threading.Thread] = None
        self._releases_cache: list = []
        self._releases_cached_at = 0.0
        self._stop_event = threading.Event()
        self._auto_check_thread: Optional[threading.Thread] = None

        if auto_check:
            self._auto_check_thread = threading.Thread(
                target=self._auto_check_loop,
                daemon=True,
                name="bambucam-update-check",
            )
            self._auto_check_thread.start()

    def check(self) -> UpdateStatus:
        """Synchronously check GitHub for the newest suitable release."""
        with self._lock:
            if self._is_update_active():
                return copy.deepcopy(self._status)
            self._status.state = UpdateState.CHECKING
            self._status.message = "Prüfe auf Updates…"
            self._status.error = ""

        try:
            release = self._fetch_latest_release()
        except Exception as exc:
            self._set_error(f"Verbindung zu GitHub fehlgeschlagen: {exc}")
            return self.status

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
            return copy.deepcopy(self._status)

    def list_releases(self) -> list:
        """Return suitable releases, with a short-lived stale-on-error cache."""
        now = time.time()
        with self._lock:
            if self._releases_cache and now - self._releases_cached_at < self._RELEASES_CACHE_TTL:
                return copy.deepcopy(self._releases_cache)

        try:
            releases = self._fetch_all_releases()
        except Exception as exc:
            log.warning("Failed to fetch releases list: %s", exc)
            with self._lock:
                return copy.deepcopy(self._releases_cache)

        with self._lock:
            self._releases_cache = releases
            self._releases_cached_at = now
            return copy.deepcopy(releases)

    def start_update(self, target_version: Optional[str] = None) -> bool:
        """Reserve and start one update worker; concurrent starts are rejected."""
        with self._lock:
            if self._is_update_active():
                log.warning("Update already in progress")
                return False

            if target_version is None:
                release = self._status.latest_release
                if not self._status.update_available or release is None:
                    log.warning("No update available — call check() first")
                    return False
            else:
                release = None

            # Reserve the operation before any network lookup. This closes the
            # race where two callers previously passed the guard simultaneously.
            self._status.state = UpdateState.DOWNLOADING
            self._status.progress = 1
            self._status.message = "Bereite Update vor…"
            self._status.error = ""

        if target_version is not None:
            release = self._find_release(target_version)
            if release is None:
                self._set_error(f"Release v{target_version.lstrip('v')} nicht gefunden")
                return False

        assert release is not None
        with self._lock:
            self._status.latest_release = release
            self._status.latest_version = release.version
            self._status.update_available = True
            self._worker = threading.Thread(
                target=self._update_pipeline,
                args=(release,),
                daemon=True,
                name="bambucam-updater",
            )
            worker = self._worker
        worker.start()
        return True

    def stop(self) -> None:
        """Stop only the optional auto-check loop; an active install is not aborted."""
        self._stop_event.set()

    @property
    def status(self) -> UpdateStatus:
        with self._lock:
            return copy.deepcopy(self._status)

    def _is_update_active(self) -> bool:
        return self._status.state.value in _ACTIVE_STATES

    def _update_pipeline(self, release: ReleaseInfo) -> None:
        temp_root: Optional[Path] = None
        try:
            self._set_state(UpdateState.DOWNLOADING, "Lade Update herunter…", 5)
            package_path = self._download(release)
            temp_root = package_path.parent

            self._set_state(UpdateState.INSTALLING, "Installiere Update…", 50)
            self._install(package_path, expected_version=release.version)

            self._set_state(UpdateState.RESTARTING, "Starte BambuCam neu…", 90)
            self._restart()

            with self._lock:
                self._current = release.version
                self._status.state = UpdateState.SUCCESS
                self._status.current_version = release.version
                self._status.update_available = False
                self._status.progress = 100
                self._status.message = (
                    f"Update auf v{release.version} erfolgreich! BambuCam wird neu gestartet…"
                )
        except Exception as exc:
            log.exception("Update pipeline failed")
            self._set_error(str(exc))
        finally:
            if temp_root is not None:
                shutil.rmtree(temp_root, ignore_errors=True)
            with self._lock:
                self._worker = None

    def _download(self, release: ReleaseInfo) -> Path:
        """Download a release wheel/sdist, enforce limits, and verify SHA-256 when provided."""
        temp_dir = Path(tempfile.mkdtemp(prefix="bambucam_update_"))
        selected = self._select_package_asset(release)
        if selected is None:
            selected = {
                "name": f"bambucam-{release.version}.tar.gz",
                "url": release.tarball_url,
                "size": 0,
                "source_tarball": True,
            }
            log.warning("No packaged release asset found; using unchecksummed GitHub source tarball")

        asset_size = int(selected.get("size") or 0)
        if asset_size > self._max_package_bytes:
            raise RuntimeError(
                f"Update package is too large ({asset_size} bytes; limit {self._max_package_bytes})"
            )

        filename = Path(str(selected["name"])).name
        if filename != selected["name"]:
            raise RuntimeError("Release asset has an unsafe filename")
        local_path = temp_dir / filename
        expected_hashes = self._fetch_release_checksums(release)
        expected_hash = expected_hashes.get(filename)
        if expected_hashes and expected_hash is None:
            raise RuntimeError(f"SHA256SUMS does not contain {filename}")

        actual_hash = self._download_to_file(
            str(selected["url"]),
            local_path,
            max_bytes=self._max_package_bytes,
            expected_size=asset_size,
        )
        if expected_hash is not None and not secrets_compare(actual_hash, expected_hash):
            raise RuntimeError(
                f"SHA-256 mismatch for {filename}: expected {expected_hash}, got {actual_hash}"
            )
        if expected_hash is None:
            log.warning("No SHA-256 checksum available for %s", filename)
        else:
            log.info("Verified SHA-256 for %s", filename)
        return local_path

    @staticmethod
    def _select_package_asset(release: ReleaseInfo) -> Optional[dict]:
        wheels = [asset for asset in release.assets if str(asset.get("name", "")).endswith(".whl")]
        if wheels:
            return wheels[0]
        sdists = [
            asset
            for asset in release.assets
            if str(asset.get("name", "")).endswith((".tar.gz", ".tgz"))
        ]
        return sdists[0] if sdists else None

    def _fetch_release_checksums(self, release: ReleaseInfo) -> dict[str, str]:
        asset = next(
            (
                item
                for item in release.assets
                if str(item.get("name", "")).upper() in {"SHA256SUMS", "SHA256SUMS.TXT"}
            ),
            None,
        )
        if asset is None:
            return {}

        response = requests.get(str(asset["url"]), stream=True, timeout=(10, 30))
        response.raise_for_status()
        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_CHECKSUM_BYTES:
                raise RuntimeError("SHA256SUMS asset exceeds the size limit")
            chunks.append(chunk)
        return _parse_checksum_file(b"".join(chunks).decode("utf-8"))

    def _download_to_file(
        self,
        url: str,
        destination: Path,
        max_bytes: int,
        expected_size: int = 0,
    ) -> str:
        response = requests.get(url, stream=True, timeout=(10, 60))
        response.raise_for_status()
        declared_size = int(response.headers.get("content-length", 0) or 0)
        if declared_size > max_bytes:
            raise RuntimeError(
                f"Download is too large ({declared_size} bytes; limit {max_bytes})"
            )
        if expected_size and declared_size and declared_size != expected_size:
            raise RuntimeError(
                f"Release asset size changed: expected {expected_size}, server reports {declared_size}"
            )

        digest = hashlib.sha256()
        downloaded = 0
        with destination.open("xb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                downloaded += len(chunk)
                if downloaded > max_bytes:
                    raise RuntimeError(f"Download exceeded the {max_bytes}-byte limit")
                handle.write(chunk)
                digest.update(chunk)
                if declared_size:
                    progress = 5 + int(40 * downloaded / declared_size)
                    self._set_progress(min(progress, 45), f"Lade herunter… {downloaded // 1024} KB")

        if expected_size and downloaded != expected_size:
            raise RuntimeError(
                f"Release asset size mismatch: expected {expected_size}, downloaded {downloaded}"
            )
        if downloaded == 0:
            raise RuntimeError("Downloaded update package is empty")
        log.info("Download complete: %s (%d bytes)", destination, downloaded)
        return digest.hexdigest()

    def _install(self, package_path: Path, expected_version: Optional[str] = None) -> None:
        """Safely extract source archives and install the package into the active venv."""
        pip = self._pip_path if self._pip_path.exists() else Path(sys.executable).parent / "pip"
        install_target = package_path

        if package_path.name.endswith((".tar.gz", ".tgz")):
            extract_dir = package_path.parent / "src"
            extract_dir.mkdir(exist_ok=True)
            _safe_extract_tar(package_path, extract_dir)
            candidates = [path for path in extract_dir.iterdir() if path.is_dir()]
            if len(candidates) != 1 or not (candidates[0] / "pyproject.toml").is_file():
                raise RuntimeError("Source archive does not contain one valid Python project")
            install_target = candidates[0]
            log.info("Source archive safely extracted to %s", install_target)

        command = [
            str(pip),
            "install",
            "--upgrade",
            "--no-user",
            "--disable-pip-version-check",
            str(install_target),
        ]
        environment = os.environ.copy()
        environment["HOME"] = "/tmp"
        environment["PIP_NO_CACHE_DIR"] = "1"

        self._set_progress(55, "Installiere Python-Paket…")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300,
            env=environment,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"pip install failed (exit {result.returncode}):\n{result.stderr[-4000:]}"
            )
        log.info("pip install succeeded")
        self._verify_installed_package(pip, expected_version)
        self._set_progress(85, "Installation geprüft und abgeschlossen.")

    @staticmethod
    def _verify_installed_package(pip: Path, expected_version: Optional[str]) -> None:
        python = pip.parent / "python"
        if not python.exists():
            python = Path(sys.executable)
        command = [
            str(python),
            "-c",
            "from importlib.metadata import version; print(version('bambucam'))",
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        if result.returncode != 0:
            raise RuntimeError(f"Installed BambuCam package cannot be imported: {result.stderr}")
        installed = result.stdout.strip()
        if expected_version and installed != expected_version:
            raise RuntimeError(
                f"Installed version mismatch: expected {expected_version}, found {installed}"
            )

    def _restart(self) -> None:
        self._set_progress(92, "Neustart wird eingeleitet…")
        if shutil.which("systemctl"):
            result = subprocess.run(
                ["systemctl", "restart", "bambucam"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                log.info("systemd restart triggered")
                return
            log.warning("systemd restart failed: %s", result.stderr.strip())

        log.warning("systemctl restart unavailable — re-execing process")
        time.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def _auto_check_loop(self) -> None:
        if self._stop_event.wait(self._AUTO_CHECK_INITIAL_DELAY):
            return
        while not self._stop_event.is_set():
            try:
                self.check()
            except Exception as exc:
                log.debug("Auto update check failed: %s", exc)
            self._stop_event.wait(self._AUTO_CHECK_INTERVAL)

    def _github_headers(self) -> dict:
        return {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "BambuCam-Updater",
        }

    def _fetch_all_releases(self) -> list:
        response = requests.get(
            f"{GITHUB_API}/repos/{self._repo}/releases",
            headers=self._github_headers(),
            timeout=(10, 30),
        )
        response.raise_for_status()
        return [
            {
                "version": release["tag_name"].lstrip("v"),
                "tag": release["tag_name"],
                "name": release.get("name") or release["tag_name"],
                "published_at": release.get("published_at", ""),
                "html_url": release["html_url"],
                "is_prerelease": release.get("prerelease", False),
                "is_current": release["tag_name"].lstrip("v") == self._current,
            }
            for release in response.json()
            if not release.get("draft")
            and (self._include_prerelease or not release.get("prerelease"))
        ]

    def _find_release(self, version: str) -> Optional[ReleaseInfo]:
        tag = f"v{version.lstrip('v')}"
        try:
            response = requests.get(
                f"{GITHUB_API}/repos/{self._repo}/releases/tags/{tag}",
                headers=self._github_headers(),
                timeout=(10, 30),
            )
            response.raise_for_status()
            return _release_from_api(response.json())
        except Exception as exc:
            log.warning("Could not fetch release %s: %s", tag, exc)
            return None

    def _fetch_latest_release(self) -> ReleaseInfo:
        response = requests.get(
            f"{GITHUB_API}/repos/{self._repo}/releases",
            headers=self._github_headers(),
            timeout=(10, 30),
        )
        response.raise_for_status()
        for release in response.json():
            if release.get("draft"):
                continue
            if release.get("prerelease") and not self._include_prerelease:
                continue
            return _release_from_api(release)
        raise RuntimeError("No suitable release found")

    def _set_state(self, state: UpdateState, message: str, progress: int) -> None:
        with self._lock:
            self._status.state = state
            self._status.message = message
            self._status.progress = progress
        log.info("[updater] %s — %s", state.value, message)

    def _set_progress(self, progress: int, message: str = "") -> None:
        with self._lock:
            self._status.progress = max(0, min(100, int(progress)))
            if message:
                self._status.message = message

    def _set_error(self, error: str) -> None:
        with self._lock:
            self._status.state = UpdateState.ERROR
            self._status.error = error
            self._status.message = f"Fehler: {error}"
            self._status.progress = 0
        log.error("[updater] %s", error)


def _release_from_api(release: dict) -> ReleaseInfo:
    return ReleaseInfo(
        version=release["tag_name"].lstrip("v"),
        tag=release["tag_name"],
        name=release.get("name") or release["tag_name"],
        body=release.get("body") or "",
        published_at=release.get("published_at", ""),
        tarball_url=release["tarball_url"],
        html_url=release["html_url"],
        is_prerelease=release.get("prerelease", False),
        assets=[
            {
                "name": asset["name"],
                "url": asset["browser_download_url"],
                "size": asset.get("size", 0),
            }
            for asset in release.get("assets", [])
        ],
    )


def _parse_checksum_file(content: str) -> dict[str, str]:
    checksums = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise RuntimeError("Invalid SHA256SUMS line")
        digest, filename = parts
        filename = filename.lstrip("* ")
        if len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
            raise RuntimeError("Invalid SHA-256 digest in SHA256SUMS")
        if Path(filename).name != filename:
            raise RuntimeError("Unsafe filename in SHA256SUMS")
        checksums[filename] = digest.lower()
    return checksums


def _safe_extract_tar(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with tarfile.open(archive, mode="r:*") as tar:
        members = tar.getmembers()
        if not members:
            raise RuntimeError("Source archive is empty")
        for member in members:
            target = (root / member.name).resolve()
            if target != root and root not in target.parents:
                raise RuntimeError(f"Unsafe path in source archive: {member.name}")
            if member.issym() or member.islnk() or member.isdev():
                raise RuntimeError(f"Unsupported special file in source archive: {member.name}")
        tar.extractall(root, members=members)


def secrets_compare(left: str, right: str) -> bool:
    import secrets

    return secrets.compare_digest(left.lower(), right.lower())


def _parse_version(version: str) -> tuple:
    value = version.lstrip("v").split("-")[0]
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        return (0,)


def _version_gt(left: str, right: str) -> bool:
    return _parse_version(left) > _parse_version(right)
