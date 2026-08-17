"""Tests for RTSP runtime configuration."""

import logging
import subprocess
import time
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


def test_crash_loop_not_detected_below_threshold():
    streamer = _streamer()
    now = time.monotonic()
    streamer._restart_timestamps = [now - 1, now - 2, now - 3, now - 4]
    assert streamer._publisher_crash_looping() is False
    assert len(streamer._restart_timestamps) == 4


def test_crash_loop_detected_at_threshold():
    streamer = _streamer()
    now = time.monotonic()
    streamer._restart_timestamps = [now - offset for offset in range(5)]
    assert streamer._publisher_crash_looping() is True


def test_crash_loop_prunes_timestamps_outside_window():
    streamer = _streamer()
    now = time.monotonic()
    window = RTSPStreamer._RESTART_BACKOFF_WINDOW
    streamer._restart_timestamps = [now - window - offset for offset in range(1, 6)] + [now - 1]
    assert streamer._publisher_crash_looping() is False
    assert streamer._restart_timestamps == [now - 1]


def test_crashed_publisher_is_stopped_before_restart():
    """Regression: restarting without stopping leaked ffmpeg/encoder resources."""
    streamer = _streamer()
    streamer._running = True
    streamer._mediamtx_proc = MagicMock(**{"poll.return_value": None})

    calls = []
    streamer._publisher_alive = MagicMock(return_value=False)
    streamer._stop_publisher = MagicMock(side_effect=lambda **kw: calls.append("stop"))
    streamer._start_publisher = MagicMock(side_effect=lambda: calls.append("start"))

    def _stop_after_one_iteration(_seconds):
        # First sleep starts the iteration, the second one ends the loop.
        if calls:
            streamer._running = False

    with patch("bambucam.streaming.rtsp.time.sleep", side_effect=_stop_after_one_iteration):
        streamer._monitor_loop()

    assert calls == ["stop", "start"]
    assert streamer._stop_publisher.call_args.kwargs == {"clear_url": False}
    assert len(streamer._restart_timestamps) == 1


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


_ENCODERS_WITH_HW = (
    b"Encoders:\n"
    b" V....D h264_omx             OpenMAX IL H.264 video encoder\n"
    b" V..... h264_v4l2m2m         V4L2 mem2mem H.264 encoder wrapper (codec h264)\n"
    b" V..... libx264              libx264 H.264 / AVC / MPEG-4 AVC\n"
)
_ENCODERS_WITHOUT_HW = b"Encoders:\n V..... libx264              libx264 H.264 / AVC / MPEG-4 AVC\n"


def _start_and_capture(streamer, run_mock=None):
    """Run _start_ffmpeg with mocked subprocess and return the ffmpeg argv."""
    process = MagicMock()
    process.poll.return_value = None
    with patch("bambucam.streaming.rtsp.subprocess.Popen", return_value=process) as popen:
        if run_mock is None:
            streamer._start_ffmpeg()
        else:
            with patch("bambucam.streaming.rtsp.subprocess.run", run_mock):
                streamer._start_ffmpeg()
    return popen.call_args.args[0]


def _run_result(stdout: bytes, returncode: int = 0) -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout)


def test_hardware_encoder_used_when_ffmpeg_advertises_it():
    streamer = _streamer()
    run = MagicMock(return_value=_run_result(_ENCODERS_WITH_HW))
    command = _start_and_capture(streamer, run)

    assert "h264_v4l2m2m" in command
    assert "libx264" not in command
    # libx264-private options must never reach the V4L2 encoder.
    assert "-preset" not in command
    assert "ultrafast" not in command
    assert "-tune" not in command
    assert "zerolatency" not in command
    # The V4L2 wrapper ignores HRD rate control, so it is dropped as well.
    assert "-maxrate" not in command
    assert "-bufsize" not in command
    assert command[command.index("-b:v") + 1] == "2500k"
    assert command[command.index("-num_output_buffers") + 1] == "32"
    assert command[command.index("-num_capture_buffers") + 1] == "16"
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    # Keyframe interval stays at 2 seconds, as with libx264.
    assert command[command.index("-g") + 1] == "60"


def test_probe_command_targets_configured_ffmpeg_binary():
    streamer = _streamer()
    run = MagicMock(return_value=_run_result(_ENCODERS_WITH_HW))
    _start_and_capture(streamer, run)

    assert run.call_args.args[0] == ["/opt/ffmpeg", "-hide_banner", "-encoders"]
    assert run.call_args.kwargs["timeout"] > 0


def test_software_encoder_used_when_hardware_encoder_absent():
    streamer = _streamer()
    run = MagicMock(return_value=_run_result(_ENCODERS_WITHOUT_HW))
    command = _start_and_capture(streamer, run)

    assert "libx264" in command
    assert "h264_v4l2m2m" not in command
    assert command[command.index("-preset") + 1] == "ultrafast"
    assert command[command.index("-tune") + 1] == "zerolatency"
    assert command[command.index("-b:v") + 1] == "2500k"
    assert command[command.index("-maxrate") + 1] == "2500k"
    assert command[command.index("-bufsize") + 1] == "5000k"
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert command[command.index("-g") + 1] == "60"


def test_non_zero_probe_exit_falls_back_to_software_encoder():
    streamer = _streamer()
    run = MagicMock(return_value=_run_result(_ENCODERS_WITH_HW, returncode=1))
    command = _start_and_capture(streamer, run)

    assert "libx264" in command
    assert "h264_v4l2m2m" not in command


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("no such file: /opt/ffmpeg"),
        OSError("permission denied"),
        subprocess.TimeoutExpired(cmd="ffmpeg", timeout=3.0),
        subprocess.SubprocessError("boom"),
    ],
)
def test_probe_failures_fall_back_to_software_encoder(failure):
    streamer = _streamer()
    run = MagicMock(side_effect=failure)
    command = _start_and_capture(streamer, run)

    assert "libx264" in command
    assert "h264_v4l2m2m" not in command


def test_encoder_detection_runs_only_once_across_restarts():
    streamer = _streamer()
    run = MagicMock(return_value=_run_result(_ENCODERS_WITH_HW))

    first = _start_and_capture(streamer, run)
    second = _start_and_capture(streamer, run)

    assert run.call_count == 1
    assert first == second
    assert "h264_v4l2m2m" in second


def test_failed_detection_is_cached_and_not_retried():
    streamer = _streamer()
    run = MagicMock(side_effect=OSError("gone"))

    _start_and_capture(streamer, run)
    command = _start_and_capture(streamer, run)

    assert run.call_count == 1
    assert "libx264" in command


def test_hw_encoder_false_skips_detection_entirely():
    streamer = _streamer(hw_encoder=False)
    run = MagicMock(return_value=_run_result(_ENCODERS_WITH_HW))
    command = _start_and_capture(streamer, run)

    assert run.call_count == 0
    assert "libx264" in command
    assert command[command.index("-preset") + 1] == "ultrafast"


def test_hw_encoder_true_forces_hardware_without_detection():
    streamer = _streamer(hw_encoder=True)
    run = MagicMock(return_value=_run_result(_ENCODERS_WITHOUT_HW))
    command = _start_and_capture(streamer, run)

    assert run.call_count == 0
    assert "h264_v4l2m2m" in command
    assert "-preset" not in command


def test_selected_encoder_is_logged_once(caplog):
    streamer = _streamer(hw_encoder=True)
    with caplog.at_level(logging.INFO, logger="bambucam.streaming.rtsp"):
        _start_and_capture(streamer)
        _start_and_capture(streamer)

    messages = [record.getMessage() for record in caplog.records if "video encoder" in record.msg]
    assert len(messages) == 1
    assert "h264_v4l2m2m" in messages[0]


def test_status_reports_the_encode_stream_resolution_for_picamera2():
    """The WebUI must not claim the still-capture resolution goes out over RTSP."""
    backend = MagicMock()
    backend.encode_resolution = (1280, 720)
    backend.is_rtsp_recording = True
    streamer = _streamer(resolution="1920x1080", camera_backend=backend)

    assert streamer.status()["resolution"] == "1280x720"


def test_status_reports_the_configured_resolution_without_picamera2():
    streamer = _streamer(resolution="1920x1080", capture_fn=lambda: b"jpeg")

    assert streamer.status()["resolution"] == "1920x1080"
