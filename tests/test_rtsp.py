"""Tests for RTSP runtime configuration."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from bambucam.streaming.rtsp import RTSPStreamer


def _streamer(**overrides) -> RTSPStreamer:
    values = {
        "v4l2_device": "/dev/video9",
        "resolution": "1280x720",
        "framerate": 30,
        "bitrate_kbps": 2500,
        "stream_name": "printer",
        "mediamtx_path": Path("/usr/local/bin/mediamtx"),
        "ffmpeg_path": "/opt/ffmpeg",
        "rtsp_port": 9554,
        "hls_port": 9888,
        "webrtc_port": 9889,
        "enable_hls": True,
        "enable_webrtc": True,
    }
    values.update(overrides)
    return RTSPStreamer(**values)


def test_stream_urls_use_configured_ports():
    streamer = _streamer()
    assert streamer.stream_urls("camera.local") == {
        "rtsp": "rtsp://camera.local:9554/printer",
        "hls": "http://camera.local:9888/printer/index.m3u8",
        "webrtc": "http://camera.local:9889/printer",
    }


def test_duplicate_ports_are_rejected():
    with pytest.raises(ValueError, match="must be different"):
        _streamer(hls_port=9554)


def test_mediamtx_config_contains_runtime_addresses():
    streamer = _streamer(rtsp_auth_user="viewer", rtsp_auth_pass="secret")
    path = streamer._write_mediamtx_config()
    try:
        config = yaml.safe_load(path.read_text())
        assert config["rtspAddress"] == ":9554"
        assert config["hlsAddress"] == ":9888"
        assert config["webrtcAddress"] == ":9889"
        assert config["paths"] == {"printer": {"source": "publisher"}}
        assert config["authInternalUsers"][0]["user"] == "viewer"
    finally:
        path.unlink(missing_ok=True)


def test_ffmpeg_command_uses_configured_binary_and_publish_port():
    streamer = _streamer(enable_webrtc=False)
    process = MagicMock()
    process.poll.return_value = None
    with patch("bambucam.streaming.rtsp.subprocess.Popen", return_value=process) as popen:
        streamer._start_ffmpeg()

    command = popen.call_args.args[0]
    assert command[0] == "/opt/ffmpeg"
    assert "rtsp://127.0.0.1:9554/printer" == command[-1]
    assert "/dev/video9" in command


def test_capture_function_selects_frame_pipe_mode():
    streamer = _streamer(capture_fn=lambda: b"jpeg")
    assert streamer._uses_frame_pipe() is True


def test_runtime_network_update_changes_urls_when_stopped():
    streamer = _streamer()
    streamer.update_settings(
        stream_name="side",
        rtsp_port=10554,
        hls_port=10888,
        webrtc_port=10889,
        enable_webrtc=False,
    )
    assert streamer.stream_urls("pi") == {
        "rtsp": "rtsp://pi:10554/side",
        "hls": "http://pi:10888/side/index.m3u8",
    }
