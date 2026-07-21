"""Tests for REST configuration validation and runtime application."""

from copy import deepcopy
from unittest.mock import MagicMock

from flask import Flask

from bambucam.config import DEFAULTS, _deep_merge
from bambucam.web.api import api_bp
from bambucam.web.security import is_password_hash


class MemoryConfig:
    def __init__(self, save_error=None):
        self.data = deepcopy(DEFAULTS)
        self.saved = 0
        self.save_error = save_error

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
        if self.save_error:
            raise self.save_error

    def replace(self, data):
        self.data = deepcopy(data)

    def as_dict(self):
        return deepcopy(self.data)


def _app(config: MemoryConfig):
    app = Flask(__name__)
    camera = MagicMock()
    camera.current_framerate = 30
    camera.current_resolution = "1920x1080"
    camera.status.return_value = {}
    mjpeg = MagicMock()
    mjpeg.is_running = True
    rtsp = MagicMock()
    rtsp.is_running = False
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


def test_rtsp_runtime_settings_are_persisted():
    config = MemoryConfig()
    app, _camera, _mjpeg, rtsp = _app(config)

    response = app.test_client().post(
        "/api/v1/stream/rtsp/settings",
        json={
            "resolution": "1280x720",
            "framerate": 15,
            "bitrate_kbps": 1200,
            "stream_name": "printer",
            "enable_hls": False,
        },
    )

    assert response.status_code == 200
    assert response.get_json()["persisted"] == [
        "bitrate_kbps",
        "enable_hls",
        "stream_name",
    ]
    assert config.data["streaming"]["rtsp"]["bitrate_kbps"] == 1200
    assert config.data["streaming"]["rtsp"]["stream_name"] == "printer"
    assert config.data["streaming"]["rtsp"]["enable_hls"] is False
    assert config.saved == 1
    rtsp.update_settings.assert_called_once_with(
        resolution="1280x720",
        framerate=15,
        bitrate_kbps=1200,
        stream_name="printer",
        rtsp_port=None,
        hls_port=None,
        webrtc_port=None,
        enable_hls=False,
        enable_webrtc=None,
    )


def test_invalid_rtsp_runtime_settings_are_not_applied_or_persisted():
    config = MemoryConfig()
    app, _camera, _mjpeg, rtsp = _app(config)

    response = app.test_client().post(
        "/api/v1/stream/rtsp/settings",
        json={"bitrate_kbps": 99},
    )

    assert response.status_code == 400
    assert "between 100 and 100000" in response.get_json()["error"]
    assert config.saved == 0
    rtsp.update_settings.assert_not_called()


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


def test_runtime_failure_does_not_persist_config():
    config = MemoryConfig()
    original = deepcopy(config.data)
    app, _camera, _mjpeg, rtsp = _app(config)
    rtsp.update_settings.side_effect = [RuntimeError("publisher failed"), None]

    response = app.test_client().post(
        "/api/v1/config",
        json={"streaming": {"rtsp": {"bitrate_kbps": 3500}}},
    )

    assert response.status_code == 400
    assert config.data == original
    assert config.saved == 0
    assert rtsp.update_settings.call_count == 2


def test_save_failure_rolls_back_runtime_and_in_memory_config():
    config = MemoryConfig(save_error=OSError("disk full"))
    original = deepcopy(config.data)
    app, _camera, _mjpeg, rtsp = _app(config)

    response = app.test_client().post(
        "/api/v1/config",
        json={"streaming": {"rtsp": {"bitrate_kbps": 3500}}},
    )

    assert response.status_code == 400
    assert config.data == original
    assert rtsp.update_settings.call_count == 2


def test_rtsp_start_persists_enabled_state_in_one_transaction():
    config = MemoryConfig()
    config.data["streaming"]["rtsp"]["enabled"] = False
    app, _camera, _mjpeg, rtsp = _app(config)

    response = app.test_client().post("/api/v1/stream/rtsp/start")

    assert response.status_code == 200
    assert response.get_json()["persisted"] is True
    assert config.data["streaming"]["rtsp"]["enabled"] is True
    assert config.saved == 1
    rtsp.start.assert_called_once_with()


def test_rtsp_stop_restarts_stream_when_persistence_fails():
    config = MemoryConfig(save_error=OSError("disk full"))
    config.data["streaming"]["rtsp"]["enabled"] = True
    app, _camera, _mjpeg, rtsp = _app(config)
    rtsp.is_running = True

    response = app.test_client().post("/api/v1/stream/rtsp/stop")

    assert response.status_code == 500
    assert config.data["streaming"]["rtsp"]["enabled"] is True
    rtsp.stop.assert_called_once_with()
    rtsp.start.assert_called_once_with()


def test_mjpeg_enabled_change_is_applied_live_and_saved():
    config = MemoryConfig()
    app, _camera, mjpeg, _rtsp = _app(config)

    response = app.test_client().post(
        "/api/v1/config",
        json={"streaming": {"mjpeg": {"enabled": False}}},
    )

    assert response.status_code == 200
    assert config.data["streaming"]["mjpeg"]["enabled"] is False
    mjpeg.stop.assert_called_once_with()


def test_application_restart_endpoint_schedules_restart():
    config = MemoryConfig()
    app, *_ = _app(config)
    updater = app.config["updater"]
    updater.restart_service.return_value = True

    response = app.test_client().post("/api/v1/system/restart")

    assert response.status_code == 202
    updater.restart_service.assert_called_once_with()
