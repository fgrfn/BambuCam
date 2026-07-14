"""Tests for snapshot persistence, retention, and HTTP routes."""

import os
import time
from pathlib import Path

import pytest
from flask import Flask

from bambucam.streaming.snapshot import SnapshotService
from bambucam.web.stream import stream_bp


@pytest.fixture
def snapshot_service(tmp_path: Path) -> SnapshotService:
    return SnapshotService(lambda: b"jpeg-data", snapshot_dir=tmp_path)


def test_capture_can_persist_snapshot(snapshot_service: SnapshotService):
    assert snapshot_service.capture(save=True) == b"jpeg-data"

    snapshots = snapshot_service.list_snapshots()
    assert len(snapshots) == 1
    assert snapshots[0]["filename"].endswith(".jpg")
    assert snapshots[0]["size"] == len(b"jpeg-data")
    assert snapshots[0]["size_kb"] == pytest.approx(len(b"jpeg-data") / 1024, abs=0.1)


def test_empty_capture_is_rejected(tmp_path: Path):
    service = SnapshotService(lambda: b"", snapshot_dir=tmp_path)
    with pytest.raises(RuntimeError, match="empty snapshot"):
        service.capture(save=True)
    assert service.list_snapshots() == []


def test_snapshot_resolution_blocks_path_traversal(snapshot_service: SnapshotService):
    with pytest.raises(ValueError):
        snapshot_service.resolve_snapshot("../secret.jpg")
    with pytest.raises(ValueError):
        snapshot_service.resolve_snapshot("not-a-jpeg.txt")


def test_snapshot_can_be_deleted(snapshot_service: SnapshotService):
    snapshot_service.capture(save=True)
    filename = snapshot_service.list_snapshots()[0]["filename"]

    snapshot_service.delete_snapshot(filename)

    assert snapshot_service.list_snapshots() == []


def _write_snapshot(directory: Path, name: str, payload: bytes, modified: float) -> Path:
    path = directory / name
    path.write_bytes(payload)
    os.utime(path, (modified, modified))
    return path


def test_retention_prunes_oldest_files_by_count(tmp_path: Path):
    now = time.time()
    _write_snapshot(tmp_path, "snapshot_old.jpg", b"1", now - 30)
    _write_snapshot(tmp_path, "snapshot_mid.jpg", b"2", now - 20)
    _write_snapshot(tmp_path, "snapshot_new.jpg", b"3", now - 10)
    service = SnapshotService(lambda: b"new", snapshot_dir=tmp_path, max_count=2)

    deleted = service.prune(now=now)

    assert deleted == ["snapshot_old.jpg"]
    assert [item["filename"] for item in service.list_snapshots()] == [
        "snapshot_mid.jpg",
        "snapshot_new.jpg",
    ]


def test_retention_prunes_by_age_and_bytes(tmp_path: Path):
    now = time.time()
    _write_snapshot(tmp_path, "snapshot_expired.jpg", b"old", now - 3 * 86400)
    _write_snapshot(tmp_path, "snapshot_large.jpg", b"12345", now - 10)
    _write_snapshot(tmp_path, "snapshot_latest.jpg", b"67890", now - 5)
    service = SnapshotService(
        lambda: b"new",
        snapshot_dir=tmp_path,
        max_count=0,
        max_age_days=1,
        max_bytes=5,
    )

    deleted = service.prune(now=now)

    assert deleted == ["snapshot_expired.jpg", "snapshot_large.jpg"]
    assert [item["filename"] for item in service.list_snapshots()] == ["snapshot_latest.jpg"]


def test_retention_can_be_updated_at_runtime(tmp_path: Path):
    now = time.time()
    _write_snapshot(tmp_path, "snapshot_one.jpg", b"1", now - 2)
    _write_snapshot(tmp_path, "snapshot_two.jpg", b"2", now - 1)
    service = SnapshotService(lambda: b"new", snapshot_dir=tmp_path, max_count=10)

    status = service.update_retention(max_count=1, max_age_days=0, max_bytes=0)

    assert status["count"] == 1
    assert status["max_count"] == 1
    assert service.list_snapshots()[0]["filename"] == "snapshot_two.jpg"


def _test_app(service: SnapshotService) -> Flask:
    app = Flask(__name__)
    app.config["snapshot_service"] = service
    app.config["mjpeg_streamer"] = None
    app.register_blueprint(stream_bp)
    return app


def test_http_snapshot_honors_save_query(snapshot_service: SnapshotService):
    client = _test_app(snapshot_service).test_client()

    response = client.get("/snapshot?save=true")

    assert response.status_code == 200
    assert response.data == b"jpeg-data"
    assert response.headers["X-BambuCam-Saved"] == "true"
    assert len(snapshot_service.list_snapshots()) == 1


def test_saved_snapshot_route_serves_existing_file(snapshot_service: SnapshotService):
    snapshot_service.capture(save=True)
    filename = snapshot_service.list_snapshots()[0]["filename"]
    client = _test_app(snapshot_service).test_client()

    response = client.get(f"/snapshots/{filename}")

    assert response.status_code == 200
    assert response.data == b"jpeg-data"
    assert response.mimetype == "image/jpeg"


def test_saved_snapshot_route_rejects_traversal(snapshot_service: SnapshotService):
    client = _test_app(snapshot_service).test_client()
    response = client.get("/snapshots/..%2Fsecret.jpg")
    assert response.status_code in {400, 404}
