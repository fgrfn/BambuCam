"""Snapshot service — captures and manages JPEG snapshots on demand."""

import logging
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class SnapshotService:
    """Capture, persist, list, resolve, and delete camera snapshots safely."""

    def __init__(self, capture_fn, snapshot_dir: Optional[Path] = None):
        self._capture_fn = capture_fn
        self._snapshot_dir = snapshot_dir or Path("/var/lib/bambucam/snapshots")
        self._last_snapshot: Optional[bytes] = None
        self._last_snapshot_time: Optional[float] = None

    @property
    def snapshot_dir(self) -> Path:
        return self._snapshot_dir

    def capture(self, save: bool = False) -> bytes:
        """Capture and return a JPEG snapshot, optionally persisting it."""
        frame = self._capture_fn()
        if not frame:
            raise RuntimeError("Camera returned an empty snapshot")

        self._last_snapshot = frame
        self._last_snapshot_time = time.time()

        if save:
            self._save(frame)

        return frame

    def _save(self, frame: bytes) -> Path:
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        # Include milliseconds to avoid collisions when saving rapidly.
        ms = int(time.time() * 1000) % 1000
        filename = f"snapshot_{time.strftime('%Y%m%d_%H%M%S')}_{ms:03d}.jpg"
        path = self._snapshot_dir / filename
        path.write_bytes(frame)
        log.info("Snapshot saved: %s", path)
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
        path = self.resolve_snapshot(filename)
        path.unlink()
        log.info("Snapshot deleted: %s", path)

    def list_snapshots(self) -> list:
        """Return snapshots oldest-first so the UI can select the newest tail."""
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
        return sorted(entries, key=lambda item: item["created"])
