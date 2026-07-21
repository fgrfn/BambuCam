"""Tests for diagnostics and snapshot-retention operational APIs."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from flask import Flask

from bambucam.web.operations import operations_bp


class FakeConfig:
    def __init__(self):
        self.data = {
            "web": {"auth": {"password": "secret"}},
            "system": {"diagnostics_log_lines": 10},
            "streaming": {"snapshot": {}},
        }
        self.saved = 0

    def as_dict(self):
        return deepcopy(self.data)

    def get(self, *keys, default=None):
        node = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def update_section(self, section, values):
        self.data.setdefault(section, {}).update(values)

    def save(self):
        self.saved += 1

    def replace(self, data):
        self.data = deepcopy(data)


class FakeSnapshot:
    snapshot_dir = Path("/snapshots")

    def __init__(self):
        self.retention = {
            "count": 2,
            "bytes": 300,
            "max_count": 500,
            "max_age_days": 30,
            "max_bytes": 1024,
        }
        self.pruned = ["old.jpg"]

    def list_snapshots(self):
        return [{"filename": "one.jpg", "size": 300, "created": 1}]

    def retention_status(self):
        return dict(self.retention)

    def update_retention(self, **values):
        self.retention.update(values)
        return dict(self.retention)

    def prune(self):
        return list(self.pruned)


class FakeStatus:
    def as_dict(self):
        return {"state": "idle", "update_available": False}


def _app():
    app = Flask(__name__)
    config = FakeConfig()
    snapshot = FakeSnapshot()
    app.config.update(
        bambucam_config=config,
        camera_manager=SimpleNamespace(status=lambda: {"running": True}),
        mjpeg_streamer=SimpleNamespace(is_running=True, client_count=1, actual_fps=15.0),
        rtsp_streamer=SimpleNamespace(status=lambda: {"running": False}),
        snapshot_service=snapshot,
        updater=SimpleNamespace(status=FakeStatus()),
    )
    app.register_blueprint(operations_bp, url_prefix="/api/v1")
    return app, config, snapshot


def test_diagnostics_json_redacts_credentials():
    app, _config, _snapshot = _app()
    response = app.test_client().get("/api/v1/diagnostics")

    assert response.status_code == 200
    assert response.get_json()["config"]["web"]["auth"]["password"] == "***"


def test_diagnostics_download_is_zip():
    app, _config, _snapshot = _app()
    response = app.test_client().get("/api/v1/diagnostics/download")

    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    assert response.data.startswith(b"PK")
    assert "bambucam_diagnostics_" in response.headers["Content-Disposition"]


def test_retention_update_persists_values():
    app, config, snapshot = _app()
    response = app.test_client().post(
        "/api/v1/snapshot/retention",
        json={"max_count": 100, "max_age_days": 14, "max_bytes": 2048},
    )

    assert response.status_code == 200
    assert response.get_json()["retention"]["max_count"] == 100
    assert snapshot.retention["max_age_days"] == 14
    assert config.saved == 1
    assert config.data["streaming"]["snapshot"]["max_bytes"] == 2048


def test_retention_rejects_unknown_or_negative_values():
    app, config, _snapshot = _app()
    client = app.test_client()

    assert client.post("/api/v1/snapshot/retention", json={"unknown": 1}).status_code == 400
    assert client.post("/api/v1/snapshot/retention", json={"max_count": -1}).status_code == 400
    assert config.saved == 0


def test_manual_prune_reports_deleted_files():
    app, _config, _snapshot = _app()
    response = app.test_client().post("/api/v1/snapshot/prune")

    assert response.status_code == 200
    assert response.get_json() == {
        "ok": True,
        "deleted": ["old.jpg"],
        "deleted_count": 1,
    }
