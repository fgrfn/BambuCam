"""Tests for health, diagnostics, log capture, and Prometheus output."""

import json
import logging
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bambucam.observability import (
    RingBufferLogHandler,
    diagnostics_payload,
    diagnostics_zip,
    health_payload,
    prometheus_payload,
    redact,
)


class FakeConfig:
    def __init__(self):
        self.data = {
            "web": {
                "auth": {
                    "enabled": True,
                    "password": "secret",
                    "api_token": "token",
                },
                "secret_key": "session-secret",
                "https": {"cert": "/secret/cert", "key": "/secret/key"},
            },
            "system": {"diagnostics_log_lines": 50},
        }

    def as_dict(self):
        return self.data

    def get(self, *keys, default=None):
        node = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


class FakeSnapshot:
    snapshot_dir = Path("/snapshots")

    def list_snapshots(self):
        return [
            {"filename": "one.jpg", "size": 100, "created": 1},
            {"filename": "two.jpg", "size": 200, "created": 2},
        ]


class FakeStatus:
    def as_dict(self):
        return {"state": "idle", "update_available": False}


class FakeUpdater:
    status = FakeStatus()


def _services(camera_running=True, mjpeg_running=True, rtsp_running=False):
    camera = SimpleNamespace(
        is_running=camera_running,
        status=lambda: {
            "running": camera_running,
            "model": "Camera Module 3",
            "framerate": 30,
        },
    )
    mjpeg = SimpleNamespace(
        is_running=mjpeg_running,
        client_count=2,
        actual_fps=14.8,
    )
    rtsp = SimpleNamespace(
        status=lambda: {
            "running": rtsp_running,
            "mediamtx_running": rtsp_running,
            "publisher_running": rtsp_running,
        }
    )
    return camera, mjpeg, rtsp


def test_health_is_ready_with_camera_and_mjpeg():
    payload, status = health_payload(*_services())
    assert status == 200
    assert payload["status"] == "ok"
    assert payload["ready"] is True


def test_health_is_degraded_without_camera():
    payload, status = health_payload(*_services(camera_running=False))
    assert status == 503
    assert payload["status"] == "degraded"


def test_ring_buffer_keeps_only_newest_records():
    handler = RingBufferLogHandler(capacity=20)
    logger = logging.getLogger("test-observability-buffer")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        for index in range(25):
            logger.info("message-%d", index)
    finally:
        logger.removeHandler(handler)

    lines = handler.lines()
    assert len(lines) == 20
    assert "message-5" in lines[0]
    assert "message-24" in lines[-1]


def test_redact_recursively_hides_credentials():
    result = redact(FakeConfig().as_dict())
    assert result["web"]["auth"]["password"] == "***"
    assert result["web"]["auth"]["api_token"] == "***"
    assert result["web"]["secret_key"] == "***"
    assert result["web"]["https"]["cert"] == "***"
    assert result["web"]["https"]["key"] == "***"


def test_diagnostics_payload_redacts_config_and_counts_snapshots():
    camera, mjpeg, rtsp = _services()
    with patch(
        "bambucam.observability.system_summary",
        return_value={"hostname": "pi", "cpu_usage_pct": 1.0},
    ), patch("bambucam.observability.recent_logs", return_value=["safe log"]):
        payload = diagnostics_payload(
            FakeConfig(),
            camera,
            mjpeg,
            rtsp,
            FakeSnapshot(),
            FakeUpdater(),
        )

    assert payload["snapshots"] == {
        "count": 2,
        "bytes": 300,
        "directory": "/snapshots",
    }
    assert payload["config"]["web"]["auth"]["password"] == "***"
    assert payload["logs"] == ["safe log"]


def test_diagnostics_zip_contains_json_and_logs():
    archive = diagnostics_zip({"logs": ["line one"], "config": {"ok": True}})
    with zipfile.ZipFile(BytesIO(archive)) as zipped:
        assert set(zipped.namelist()) == {"diagnostics.json", "logs.txt"}
        data = json.loads(zipped.read("diagnostics.json"))
        assert data["logs"] == ["See logs.txt"]
        assert zipped.read("logs.txt") == b"line one\n"


def test_prometheus_output_contains_core_metrics():
    camera, mjpeg, rtsp = _services(rtsp_running=True)
    system = {
        "cpu_temp_c": 42.5,
        "cpu_usage_pct": 10.0,
        "memory": {"available_mb": 256},
        "disk": {"free_gb": 12.5},
        "uptime_seconds": 123,
    }
    with patch("bambucam.observability.system_summary", return_value=system):
        payload = prometheus_payload(
            FakeConfig(),
            camera,
            mjpeg,
            rtsp,
            FakeSnapshot(),
            FakeUpdater(),
        )

    assert "bambucam_camera_running 1" in payload
    assert "bambucam_mjpeg_clients 2" in payload
    assert "bambucam_mjpeg_fps 14.8" in payload
    assert "bambucam_cpu_temperature_celsius 42.5" in payload
    assert "bambucam_snapshots_total 2" in payload
