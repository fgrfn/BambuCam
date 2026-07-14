"""Tests for snapshot persistence and HTTP routes."""

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
