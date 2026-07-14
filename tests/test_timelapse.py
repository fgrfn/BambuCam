"""Tests for timelapse capture, rendering, retention, and HTTP routes."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

from bambucam.timelapse import TimelapseService
from bambucam.web.features import features_bp


def wait_for_frames(service: TimelapseService, minimum: int = 1) -> None:
    deadline = time.time() + 3
    while service.status.frame_count < minimum and time.time() < deadline:
        time.sleep(0.02)
    assert service.status.frame_count >= minimum


def test_timelapse_captures_and_lists_session(tmp_path: Path):
    service = TimelapseService(
        capture_fn=lambda: b"jpeg-frame",
        root_dir=tmp_path,
        interval_seconds=0.5,
        render_on_stop=False,
    )

    status = service.start(title="Printer job")
    wait_for_frames(service)
    stopped = service.stop(render=False)

    assert status.session_id
    assert stopped.running is False
    assert stopped.frame_count >= 1
    sessions = service.sessions()
    assert sessions[0]["title"] == "Printer job"
    assert sessions[0]["frame_count"] >= 1
    assert (tmp_path / status.session_id / "metadata.json").is_file()


def test_render_creates_video_and_updates_metadata(tmp_path: Path, monkeypatch):
    service = TimelapseService(
        capture_fn=lambda: b"jpeg-frame",
        root_dir=tmp_path,
        interval_seconds=0.5,
        render_on_stop=False,
    )
    session_id = service.start().session_id
    wait_for_frames(service)
    service.stop(render=False)

    def fake_run(command, capture_output, text, timeout):
        assert "libx264" in command
        Path(command[-1]).write_bytes(b"mp4-data")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("bambucam.timelapse.subprocess.run", fake_run)
    session = service.render(session_id, output_fps=24)

    assert session["video_available"] is True
    assert session["video_bytes"] == len(b"mp4-data")
    assert service.video_path(session_id).read_bytes() == b"mp4-data"
    metadata = json.loads((tmp_path / session_id / "metadata.json").read_text())
    assert metadata["output_fps"] == 24


def test_render_failure_does_not_leave_partial_video(tmp_path: Path, monkeypatch):
    service = TimelapseService(
        capture_fn=lambda: b"jpeg-frame",
        root_dir=tmp_path,
        interval_seconds=0.5,
        render_on_stop=False,
    )
    session_id = service.start().session_id
    wait_for_frames(service)
    service.stop(render=False)

    monkeypatch.setattr(
        "bambucam.timelapse.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr="encoder failed"),
    )
    with pytest.raises(RuntimeError, match="encoder failed"):
        service.render(session_id)
    assert not (tmp_path / session_id / "timelapse.mp4").exists()


def test_session_id_blocks_path_traversal(tmp_path: Path):
    service = TimelapseService(lambda: b"frame", root_dir=tmp_path)
    with pytest.raises(ValueError):
        service.video_path("../secret")
    with pytest.raises(ValueError):
        service.delete("bad/name")


def test_prune_keeps_only_newest_sessions(tmp_path: Path):
    service = TimelapseService(
        lambda: b"frame",
        root_dir=tmp_path,
        max_sessions=2,
        render_on_stop=False,
    )
    for index in range(3):
        session = tmp_path / f"20260101_00000{index}"
        (session / "frames").mkdir(parents=True)
        (session / "metadata.json").write_text(
            json.dumps({"started_at": float(index), "title": str(index)}),
            encoding="utf-8",
        )

    deleted = service.prune(now=10)
    assert deleted == ["20260101_000000"]
    assert len(service.sessions()) == 2


class FakeConfig:
    def __init__(self):
        self.data = {"camera": {"active_profile": "custom"}, "streaming": {}}

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
        pass


class FakeProfiles:
    def list_profiles(self):
        return [{"name": "balanced"}]

    def apply(self, name):
        if name != "balanced":
            raise ValueError("unknown")
        return {"name": name}


@pytest.fixture
def feature_client(tmp_path: Path):
    service = TimelapseService(
        lambda: b"jpeg-frame",
        root_dir=tmp_path,
        interval_seconds=0.5,
        render_on_stop=False,
    )
    app = Flask(__name__)
    app.config["timelapse_service"] = service
    app.config["camera_profile_service"] = FakeProfiles()
    app.config["bambucam_config"] = FakeConfig()
    app.register_blueprint(features_bp, url_prefix="/api/v1")
    yield app.test_client(), service
    service.shutdown()


def test_feature_api_starts_and_stops_timelapse(feature_client):
    client, service = feature_client
    response = client.post(
        "/api/v1/timelapse/start",
        json={"title": "API job", "interval_seconds": 0.5, "output_fps": 25},
    )
    assert response.status_code == 200
    wait_for_frames(service)

    response = client.post("/api/v1/timelapse/stop", json={"render": False})
    assert response.status_code == 200
    assert response.get_json()["status"]["running"] is False


def test_feature_api_profiles_and_settings(feature_client):
    client, _ = feature_client
    response = client.get("/api/v1/camera/profiles")
    assert response.status_code == 200
    assert response.get_json()["profiles"][0]["name"] == "balanced"

    response = client.post("/api/v1/camera/profiles/balanced")
    assert response.status_code == 200

    response = client.post(
        "/api/v1/timelapse/settings",
        json={"interval_seconds": 20, "max_sessions": 5},
    )
    assert response.status_code == 200
    assert response.get_json()["settings"]["max_sessions"] == 5
