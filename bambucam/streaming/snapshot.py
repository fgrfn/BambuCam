"""Snapshot service — capture, retain, and safely serve JPEG snapshots."""

import logging
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class SnapshotService:
    """Capture snapshots and enforce count, age, and byte retention limits."""

    def __init__(
        self,
        capture_fn,
        snapshot_dir: Optional[Path] = None,
        max_count: int = 500,
        max_age_days: int = 30,
        max_bytes: int = 1024 * 1024 * 1024,
    ):
        self._capture_fn = capture_fn
        self._snapshot_dir = snapshot_dir or Path("/var/lib/bambucam/snapshots")
        self._last_snapshot: Optional[bytes] = None
        self._last_snapshot_time: Optional[float] = None
        self._max_count = max(0, int(max_count))
        self._max_age_days = max(0, int(max_age_days))
        self._max_bytes = max(0, int(max_bytes))
        self._lock = threading.RLock()

    @property
    def snapshot_dir(self) -> Path:
        return self._snapshot_dir

    def capture(self, save: bool = False) -> bytes:
        """Capture and return a JPEG snapshot, optionally persisting it."""
        frame = self._capture_fn()
        if not frame:
            raise RuntimeError("Camera returned an empty snapshot")

        with self._lock:
            self._last_snapshot = frame
            self._last_snapshot_time = time.time()
            if save:
                self._save(frame)
        return frame

    def _save(self, frame: bytes) -> Path:
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.time()
        milliseconds = int(timestamp * 1000) % 1000
        filename = f"snapshot_{time.strftime('%Y%m%d_%H%M%S')}_{milliseconds:03d}.jpg"
        path = self._snapshot_dir / filename
        temporary = path.with_suffix(".jpg.tmp")
        temporary.write_bytes(frame)
        temporary.replace(path)
        log.info("Snapshot saved: %s", path)
        self.prune(now=timestamp)
        return path

    def resolve_snapshot(self, filename: str) -> Path:
        """Resolve a snapshot filename without allowing path traversal."""
        if not filename or Path(filename).name != filename or not filename.lower().endswith(".jpg"):
            raise ValueError("Invalid snapshot filename")

        path = self._snapshot_dir / filename
        if not path.is_file():
            raise FileNotFoundError(filename)
        return path

    def delete_snapshot(self, filename: str) -> None:
        """Delete one saved snapshot by its safe basename."""
        with self._lock:
            path = self.resolve_snapshot(filename)
            path.unlink()
        log.info("Snapshot deleted: %s", path)

    def list_snapshots(self) -> list:
        """Return snapshots oldest-first so consumers can select the newest tail."""
        if not self._snapshot_dir.exists():
            return []

        entries = []
        for file_path in self._snapshot_dir.glob("*.jpg"):
            try:
                stat = file_path.stat()
                entries.append(
                    {
                        "filename": file_path.name,
                        "size": stat.st_size,
                        "size_kb": round(stat.st_size / 1024, 1),
                        "created": stat.st_mtime,
                    }
                )
            except OSError:
                continue
        return sorted(entries, key=lambda item: (item["created"], item["filename"]))

    def retention_status(self) -> dict:
        snapshots = self.list_snapshots()
        return {
            "count": len(snapshots),
            "bytes": sum(int(item["size"]) for item in snapshots),
            "max_count": self._max_count,
            "max_age_days": self._max_age_days,
            "max_bytes": self._max_bytes,
        }

    def update_retention(
        self,
        max_count: Optional[int] = None,
        max_age_days: Optional[int] = None,
        max_bytes: Optional[int] = None,
    ) -> dict:
        """Update retention limits at runtime and immediately prune saved files."""
        with self._lock:
            if max_count is not None:
                self._max_count = max(0, int(max_count))
            if max_age_days is not None:
                self._max_age_days = max(0, int(max_age_days))
            if max_bytes is not None:
                self._max_bytes = max(0, int(max_bytes))
            self.prune()
            return self.retention_status()

    def prune(self, now: Optional[float] = None) -> list[str]:
        """Delete oldest snapshots until all enabled retention limits are met."""
        with self._lock:
            snapshots = self.list_snapshots()
            deleted: list[str] = []
            current_time = time.time() if now is None else float(now)

            if self._max_age_days > 0:
                cutoff = current_time - self._max_age_days * 86400
                for item in list(snapshots):
                    if float(item["created"]) >= cutoff:
                        continue
                    if self._unlink_item(item):
                        deleted.append(str(item["filename"]))
                    snapshots.remove(item)

            if self._max_count > 0:
                while len(snapshots) > self._max_count:
                    item = snapshots.pop(0)
                    if self._unlink_item(item):
                        deleted.append(str(item["filename"]))

            if self._max_bytes > 0:
                total_bytes = sum(int(item["size"]) for item in snapshots)
                while snapshots and total_bytes > self._max_bytes:
                    item = snapshots.pop(0)
                    if self._unlink_item(item):
                        deleted.append(str(item["filename"]))
                    total_bytes -= int(item["size"])

            if deleted:
                log.info("Snapshot retention removed %d file(s)", len(deleted))
            return deleted

    def _unlink_item(self, item: dict) -> bool:
        try:
            (self._snapshot_dir / str(item["filename"])).unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            log.warning("Failed to remove retained snapshot %s: %s", item["filename"], exc)
            return False
