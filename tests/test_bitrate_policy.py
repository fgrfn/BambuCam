"""Policy boundaries and real API/encoder integration without physical hardware."""

from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

from bambucam.camera.backends.picamera2_backend import Picamera2Backend
from bambucam.camera.models import CAMERA_V1, Resolution
from bambucam.camera.profiles import CameraProfileService
from bambucam.config import DEFAULTS, Config, migrate_config, validate_config_update
from bambucam.encoding import bitrate_limits, validate_bitrate
from bambucam.main import _clamp_rtsp_bitrate
from bambucam.streaming.rtsp import RTSPStreamer
from tests.test_api_config import MemoryConfig, _app
from tests.test_picamera2_backend import _encoder_modules


@pytest.mark.parametrize(
    "tier,expected",
    [
        (1, [2000, 3000, 3000, 3000]),
        (2, [2000, 6000, 8000, 8000]),
        (3, [2000, 6000, 12000, 20000]),
        (99, [2000, 6000, 12000, 20000]),
    ],
)
def test_hardware_and_resolution_matrix(tier, expected):
    sizes = ["640x480", "1280x720", "1920x1080", "3840x2160"]
    assert [bitrate_limits(size, tier)["max_kbps"] for size in sizes] == expected


@pytest.mark.parametrize(
    "size,maximum",
    [
        ("640x480", 2000),
        ("642x480", 6000),
        ("1280x720", 6000),
        ("1282x720", 12000),
        ("1920x1080", 12000),
        ("1922x1080", 20000),
    ],
)
def test_area_thresholds_and_inclusive_bitrate_boundaries(size, maximum):
    limits = bitrate_limits(size, 3)
    assert limits["max_kbps"] == maximum
    assert validate_bitrate(100, limits) == 100
    assert validate_bitrate(maximum, limits) == maximum
    for invalid in (99, maximum + 1):
        with pytest.raises(ValueError, match="between"):
            validate_bitrate(invalid, limits)


@pytest.mark.parametrize("value", [True, False, 1000.0, 1000.5, "1000", None, float("nan")])
def test_bitrate_requires_an_actual_integer(value):
    with pytest.raises(ValueError, match="integer"):
        validate_bitrate(value, bitrate_limits("640x480", 3))
    with pytest.raises(ValueError):
        RTSPStreamer("/dev/video0", bitrate_kbps=value)


def test_config_validation_and_legacy_normalization_use_same_policy():
    config = deepcopy(DEFAULTS)
    config["camera"]["resolution"] = "640x480"
    config["streaming"]["rtsp"]["bitrate_kbps"] = 12000
    with pytest.raises(ValueError, match="between 100 and 2000"):
        validate_config_update({}, config)
    normalized, changed = migrate_config(config)
    assert changed
    assert normalized["streaming"]["rtsp"]["bitrate_kbps"] == 2000
    assert config["streaming"]["rtsp"]["bitrate_kbps"] == 12000
    assert validate_config_update({}, normalized) == normalized


def test_legacy_limit_is_persisted_on_load(tmp_path):
    path = tmp_path / "legacy.yaml"
    path.write_text(
        "camera:\n  resolution: 640x480\nstreaming:\n  rtsp:\n    bitrate_kbps: 12000\n"
    )
    config = Config()
    with (
        patch("bambucam.config._SYSTEM_CONFIG", tmp_path / "absent"),
        patch("bambucam.config._USER_CONFIG", tmp_path / "absent-user"),
    ):
        config.load(path)
    assert config.get("streaming", "rtsp", "bitrate_kbps") == 2000
    assert "bitrate_kbps: 2000" in path.read_text()


def test_startup_clamps_to_actual_encode_size_even_with_large_main_stream():
    assert _clamp_rtsp_bitrate(20000, 3, resolution=(640, 360)) == 2000
    assert _clamp_rtsp_bitrate(20000, 2, resolution=(1920, 1080)) == 8000


def _csi_streamer():
    backend = Picamera2Backend(CAMERA_V1, "libcamera:0", encode_size=(1920, 1080))
    backend.configure(Resolution(2592, 1944), 15)
    backend._encode_size = (1920, 1080)
    streamer = RTSPStreamer(
        "libcamera:0",
        resolution="2592x1944",
        bitrate_kbps=6000,
        camera_backend=backend,
        hardware_tier=3,
    )
    return streamer, backend


def test_limits_use_actual_csi_encoder_not_main_sensor_resolution():
    streamer, _backend = _csi_streamer()
    assert streamer.bitrate_limits_for()["max_kbps"] == 12000
    assert streamer.bitrate_limits_for()["resolution"] == "1920x1080"
    assert streamer.bitrate_limits_for("640x480")["max_kbps"] == 2000
    assert streamer.status()["bitrate_limits"]["max_kbps"] == 12000


def test_rejected_runtime_update_preserves_running_publisher_and_all_settings():
    streamer, backend = _csi_streamer()
    streamer._running = True
    original = streamer._resolution, streamer._bitrate, streamer._stream_name
    with (
        patch.object(streamer, "stop") as stop,
        patch.object(streamer, "_stop_publisher") as stop_pub,
    ):
        with pytest.raises(ValueError, match="between 100 and 2000"):
            streamer.update_settings(resolution="640x480", bitrate_kbps=6000, stream_name="new")
    stop.assert_not_called()
    stop_pub.assert_not_called()
    assert streamer._running
    assert (streamer._resolution, streamer._bitrate, streamer._stream_name) == original
    assert backend.encode_resolution == (1920, 1080)


def test_direct_picamera_encoder_rejects_before_stopping_existing_encoder():
    _streamer, backend = _csi_streamer()
    backend._running = True
    backend._picam = MagicMock()
    backend._h264_encoder = object()
    modules = _encoder_modules()
    with patch.dict("sys.modules", modules), patch.object(backend, "stop_rtsp_recording") as stop:
        with pytest.raises(ValueError, match="between 100 and 12000"):
            backend.start_rtsp_recording("rtsp://localhost/cam", 12001)
    stop.assert_not_called()
    modules["picamera2.encoders"].H264Encoder.assert_not_called()


@pytest.mark.parametrize(
    "endpoint,payload",
    [
        ("/config", {"streaming": {"rtsp": {"bitrate_kbps": 12001}}}),
        ("/stream/settings", {"bitrate_kbps": 12001}),
        ("/stream/rtsp/settings", {"bitrate_kbps": 12001}),
        ("/camera/settings", {"resolution": "640x480"}),
        ("/stream/settings", {"resolution": "640x480"}),
        ("/config", {"camera": {"resolution": "640x480"}}),
        ("/stream/rtsp/settings", {"resolution": "640x480"}),
    ],
)
def test_all_api_paths_reject_before_runtime_changes(endpoint, payload):
    config = MemoryConfig()
    config.data["streaming"]["rtsp"]["bitrate_kbps"] = 6000
    app, camera, mjpeg, _ = _app(config)
    streamer, _backend = _csi_streamer()
    streamer._running = True
    app.config["rtsp_streamer"] = streamer
    original = config.as_dict()
    with (
        patch.object(streamer, "stop") as stop,
        patch.object(streamer, "_stop_publisher") as stop_pub,
    ):
        response = app.test_client().post("/api/v1" + endpoint, json=payload)
    assert response.status_code == 400
    assert "kbps" in response.get_json()["error"]
    assert config.as_dict() == original
    assert config.saved == 0
    camera.apply_settings.assert_not_called()
    mjpeg.update_fps.assert_not_called()
    stop.assert_not_called()
    stop_pub.assert_not_called()


def test_status_exposes_proposed_limits_even_when_rtsp_is_disabled():
    app, camera, mjpeg, _ = _app(MemoryConfig())
    mjpeg.client_count = 0
    mjpeg.actual_fps = 15
    streamer, _ = _csi_streamer()
    app.config["rtsp_streamer"] = streamer
    camera.status.return_value = {"available_resolutions": ["2592x1944", "640x480"]}
    data = app.test_client().get("/api/v1/stream/status").get_json()["rtsp"]
    assert not data["running"]
    assert data["bitrate_limits"]["resolution"] == "1920x1080"
    assert data["bitrate_limits_by_resolution"]["640x480"]["max_kbps"] == 2000


def test_profile_bitrate_is_capped_for_actual_encode_stream():
    from tests.test_profiles import FakeCamera, FakeConfig, FakeMJPEG

    streamer = RTSPStreamer("/dev/video0", hardware_tier=1)
    profiles = CameraProfileService(FakeConfig(), FakeCamera(), FakeMJPEG(), streamer)
    assert profiles.resolve("quality")["bitrate_kbps"] == 3000


def test_atomic_smaller_resolution_and_bitrate_are_persisted_together():
    config = MemoryConfig()
    config.data["streaming"]["rtsp"]["bitrate_kbps"] = 6000
    app, _camera, _mjpeg, _ = _app(config)
    streamer, _ = _csi_streamer()
    app.config["rtsp_streamer"] = streamer
    response = app.test_client().post(
        "/api/v1/stream/settings",
        json={
            "resolution": "640x480",
            "framerate": 15,
            "bitrate_kbps": 2000,
        },
    )
    assert response.status_code == 200
    assert streamer._resolution == "640x480"
    assert streamer._bitrate == 2000
    assert config.data["camera"]["resolution"] == "640x480"
    assert config.data["streaming"]["rtsp"]["bitrate_kbps"] == 2000
    assert config.saved == 1


def test_save_failure_restores_old_rtsp_resolution_with_its_bitrate():
    config = MemoryConfig(save_error=OSError("disk full"))
    config.data["streaming"]["rtsp"]["bitrate_kbps"] = 6000
    original = config.as_dict()
    app, camera, _mjpeg, _ = _app(config)
    camera.status.return_value = {"resolution": "2592x1944", "framerate": 15}
    streamer, _ = _csi_streamer()
    app.config["rtsp_streamer"] = streamer
    response = app.test_client().post(
        "/api/v1/stream/settings",
        json={
            "resolution": "640x480",
            "framerate": 15,
            "bitrate_kbps": 2000,
        },
    )
    assert response.status_code == 400
    assert streamer._resolution == "2592x1944"
    assert streamer._bitrate == 6000
    assert config.as_dict() == original


def test_ffmpeg_frame_pipe_output_matches_policy_resolution():
    streamer = RTSPStreamer(
        "/dev/video0",
        resolution="640x480",
        bitrate_kbps=2000,
        capture_fn=lambda: b"jpeg",
        hw_encoder=False,
    )
    with (
        patch("bambucam.streaming.rtsp.subprocess.Popen") as popen,
        patch("bambucam.streaming.rtsp.threading.Thread"),
    ):
        streamer._start_ffmpeg()
    command = popen.call_args.args[0]
    assert command[command.index("-vf") + 1] == "scale=640:480"
    assert command[command.index("-b:v") + 1] == "2000k"
