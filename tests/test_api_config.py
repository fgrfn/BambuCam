"""Tests for REST configuration validation and runtime application."""

from copy import deepcopy
from unittest.mock import MagicMock

from flask import Flask

from bambucam.config import DEFAULTS, _deep_merge
from bambucam.web.api import api_bp
from bambucam.web.security import is_password_hash


class MemoryConfig:
    def __init__(self):
        self.data = deepcopy(DEFAULTS)
        self.saved = 0

    def get(self, *keys, default=None):
        node = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def update_section(self, section, values):
        self.data[section] = _deep_merge(self.data.get(section, {}), values)

    def save(self):
        self.saved += 1

    def as_dict(self):
        return deepcopy(self.data)


def _app(config: MemoryConfig):
    app = Flask(__name__)
    camera = MagicMock()
    camera.current_framerate = 30
    camera.current_resolution = "1920x1080"
    camera.status.return_value = {}
    mjpeg = MagicMock()
    rtsp = MagicMock()
    rtsp.status.return_value = {}
    rtsp.stream_urls.return_value = {}
    snapshot = MagicMock()
    updater = MagicMock()

    app.config.update(
        bambucam_config=config,
        camera_manager=camera,
        mjpeg_streamer=mjpeg,
        rtsp_streamer=rtsp,
        snapshot_service=snapshot,
        updater=updater,
    )
    app.register_blueprint(api_bp, url_prefix="/api/v1")
    return app, camera, mjpeg, rtsp


def test_config_update_hashes_password_and_syncs_shared_web_port():
    config = MemoryConfig()
    app, _camera, mjpeg, _rtsp = _app(config)

    response = app.test_client().post(
        "/api/v1/config",
        json={
            "streaming": {"mjpeg": {"port": 9090, "quality": 80, "fps": 10}},
            "web": {
                "auth": {
                    "enabled": True,
                    "username": "admin",
                    "password": "secret",
                }
            },
        },
    )

    assert response.status_code == 200
    assert response.get_json()["restart_required"] is True
    assert config.data["web"]["port"] == 9090
    assert config.data["streaming"]["mjpeg"]["port"] == 9090
    assert is_password_hash(config.data["web"]["auth"]["password"])
    assert config.saved == 1
    mjpeg.update_fps.assert_called_once_with(10)


def test_invalid_port_is_rejected_without_saving():
    config = MemoryConfig()
    app, *_ = _app(config)

    response = app.test_client().post(
        "/api/v1/config",
        json={"streaming": {"rtsp": {"port": 70000}}},
    )

    assert response.status_code == 400
    assert "between 1 and 65535" in response.get_json()["error"]
    assert config.saved == 0


def test_config_response_redacts_all_credentials():
    config = MemoryConfig()
    config.data["web"]["auth"].update({"password": "hash", "api_token": "token"})
    config.data["streaming"]["rtsp"]["auth"].update({"password": "rtsp-secret"})
    app, *_ = _app(config)

    response = app.test_client().get("/api/v1/config")
    body = response.get_json()

    assert body["web"]["auth"]["password"] == "***"
    assert body["web"]["auth"]["api_token"] == "***"
    assert body["streaming"]["rtsp"]["auth"]["password"] == "***"
