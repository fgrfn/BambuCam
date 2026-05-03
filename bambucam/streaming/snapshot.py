"""Snapshot service — captures single JPEG frames on demand."""

import logging
import os
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class SnapshotService:
    """
    Captures and optionally saves JPEG snapshots from the camera.
    Exposes a simple REST-compatible interface.
    """

    def __init__(self, capture_fn, snapshot_dir: Optional[Path] = None):
        self._capture_fn = capture_fn
        self._snapshot_dir = snapshot_dir or Path("/var/lib/bambucam/snapshots")
        self._last_snapshot: Optional[bytes] = None
        self._last_snapshot_time: Optional[float] = None

    def capture(self, save: bool = False) -> bytes:
        """Capture and return a JPEG snapshot."""
        frame = self._capture_fn()
        self._last_snapshot = frame
        self._last_snapshot_time = time.time()

        if save:
            self._save(frame)

        return frame

    def _save(self, frame: bytes) -> Path:
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        filename = time.strftime("snapshot_%Y%m%d_%H%M%S.jpg")
        path = self._snapshot_dir / filename
        path.write_bytes(frame)
        log.info("Snapshot saved: %s", path)
        return path

    def list_snapshots(self) -> list:
        if not self._snapshot_dir.exists():
            return []
        return sorted(
            [
                {
                    "filename": f.name,
                    "size": f.stat().st_size,
                    "created": f.stat().st_mtime,
                }
                for f in self._snapshot_dir.glob("*.jpg")
            ],
            key=lambda x: x["created"],
            reverse=True,
        )
