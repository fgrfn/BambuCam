"""Regression evidence from OV5647/CM4, including a configured/busy sensor."""

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from bambucam.camera.backends.picamera2_backend import Picamera2Backend
from bambucam.camera.detector import _parse_libcamera_output, _parse_libcamera_resolutions
from bambucam.camera.manager import CameraManager
from bambucam.camera.models import CAMERA_V1, Resolution
from bambucam.camera.profiles import CameraProfileService
from bambucam.main import _resolve_camera_mode
from bambucam.web.api import api_bp

OV5647 = """Available cameras
-----------------
0 : ov5647 [2592x1944 10-bit GBRG] (/camera0)
    Modes: 'SGBRG10_CSI2P' : 640x480 [62.50 fps - (16, 0)/2560x1920 crop]
                             1296x972 [46.34 fps - (0, 0)/2592x1944 crop]
                             1920x1080 [32.81 fps - (348, 434)/1928x1080 crop]
                             2592x1944 [15.63 fps - (0, 0)/2592x1944 crop]
"""


def test_only_mode_records_are_parsed_and_decimal_fps_are_preserved():
    camera = _parse_libcamera_output(OV5647)[0]
    assert {str(m.resolution): m.max_fps for m in camera.sensor_modes} == {
        "640x480": 62.5,
        "1296x972": 46.34,
        "1920x1080": 32.81,
        "2592x1944": 15.63,
    }
    assert Resolution(2560, 1920) not in camera.detected_resolutions
    assert Resolution(1928, 1080) not in _parse_libcamera_resolutions(OV5647, "ov5647")
    assert camera.mode_source == "detected"
    assert CAMERA_V1.max_resolution == Resolution(2592, 1944)


@pytest.mark.parametrize(
    "size,limit",
    [("2592x1944", 15), ("1920x1080", 32), ("1296x972", 46), ("640x480", 62), ("1280x720", 46)],
)
def test_runtime_and_startup_use_detected_fps(size, limit):
    detected = _parse_libcamera_output(OV5647)[0]
    resolution = Resolution.from_string(size)
    CameraManager._validate_mode(resolution, limit, detected)
    with pytest.raises(ValueError, match="at most"):
        CameraManager._validate_mode(resolution, limit + 1, detected)
    resolved = _resolve_camera_mode(
        detected.model,
        {"resolution": size, "framerate": "auto"},
        resolutions=detected.detected_resolutions,
        max_framerates={
            Resolution.from_string(m["resolution"]): m["max_fps"] for m in detected.output_modes
        },
    )
    assert resolved == (resolution, limit)


def test_scaled_output_has_explicit_source_and_is_not_720p60():
    detected = _parse_libcamera_output(OV5647)[0]
    scaled = next(m for m in detected.output_modes if m["resolution"] == "1280x720")
    assert scaled["kind"] == "scaled"
    assert scaled["sensor_resolution"] == "1296x972"
    assert scaled["sensor_max_fps"] == 46.34
    assert scaled["max_fps"] == 46


def test_busy_probe_uses_labelled_fallback_instead_of_poisoned_30fps():
    import re

    busy = re.sub(r"\[[\d.]+ fps[^\]]*\]", "[30.00 fps - (65519, 65529)/65535x65535 crop]", OV5647)
    detected = _parse_libcamera_output(busy)[0]
    assert detected.mode_source == "model_fallback"
    assert detected.max_framerate_for(Resolution(2592, 1944)) == 15
    assert detected.max_framerate_for(Resolution(640, 480)) == 62
    assert Resolution(65535, 65535) not in detected.detected_resolutions


def test_same_sensor_on_two_ports_and_duplicate_formats_remain_separate():
    second = (
        OV5647.replace("0 : ov5647", "1 : ov5647")
        .replace("/camera0", "/camera1")
        .replace("62.50", "59.00")
    )
    cameras = _parse_libcamera_output(OV5647 + second)
    assert cameras[0].sensor_modes[0].max_fps == 62.5
    assert cameras[1].sensor_modes[0].max_fps == 59.0
    duplicate = OV5647 + "    Modes: 'SGBRG8' : 640x480 [60.00 fps]\n"
    camera = _parse_libcamera_output(duplicate)[0]
    assert len(camera.sensor_modes) == 5
    assert camera.max_framerate_for(Resolution(640, 480)) == 62


def test_running_detection_api_and_status_use_startup_snapshot():
    camera = CameraManager()
    detected = _parse_libcamera_output(OV5647)[0]
    camera._detected = detected
    camera._backend = MagicMock(is_running=True, supports_zoom=True, max_zoom=8.0)
    app = Flask(__name__)
    app.config["camera_manager"] = camera
    app.register_blueprint(api_bp, url_prefix="/api/v1")
    with patch("bambucam.camera.manager.detect_cameras") as probe:
        data = app.test_client().get("/api/v1/camera/detect").get_json()
        status = app.test_client().get("/api/v1/camera/status").get_json()
    probe.assert_not_called()
    assert data[0]["mode_source"] == "detected"
    assert status["resolution_max_framerates"]["2592x1944"] == 15
    assert status["snapshot_max_resolution"] == "2592x1944"
    assert "2592x1944" not in status["video_resolution_max_framerates"]
    assert status["video_resolution_max_framerates"]["1920x1080"] == 30
    assert status["video_resolution_max_framerates"]["1280x720"] == 46
    assert status["native_sensor_modes"][0]["max_fps"] == 62.5


def test_module_override_preserves_detected_modes():
    manager = CameraManager()
    with patch(
        "bambucam.camera.manager.detect_cameras", return_value=_parse_libcamera_output(OV5647)
    ):
        detected = manager.detect_and_select(module_override="v1_noir")
    assert detected.model.is_noir
    assert detected.sensor_modes[0].max_fps == 62.5


def test_profiles_use_dynamic_limits():
    camera = MagicMock(model=CAMERA_V1)
    camera.status.return_value = {
        "available_resolutions": ["2592x1944"],
        "resolution_max_framerates": {"2592x1944": 7},
    }
    profiles = CameraProfileService(None, camera, None, None)
    assert profiles.resolve("quality")["framerate"] == 7


def test_scaled_backend_configuration_pins_same_native_source_as_fps_limit():
    camera = _parse_libcamera_output(OV5647)[0]
    backend = Picamera2Backend(CAMERA_V1, "libcamera:0", sensor_modes=camera.sensor_modes)
    backend.configure(Resolution(1280, 720), 46)
    picam = MagicMock()
    package = ModuleType("picamera2")
    package.Picamera2 = MagicMock(return_value=picam)
    libcamera = ModuleType("libcamera")
    libcamera.Transform = MagicMock()
    with patch.dict(sys.modules, {"picamera2": package, "libcamera": libcamera}):
        backend.start()
    config = picam.create_video_configuration.call_args.kwargs
    assert config["main"]["size"] == (1280, 720)
    assert config["raw"] == {"size": (1296, 972), "format": "SGBRG10_CSI2P"}
    assert config["controls"]["FrameRate"] == 46.0


def test_auto_start_prefers_1080p30_video_but_explicit_still_mode_remains_available():
    detected = _parse_libcamera_output(OV5647)[0]
    limits = {
        Resolution.from_string(mode["resolution"]): mode["max_fps"]
        for mode in detected.output_modes
    }
    auto = _resolve_camera_mode(
        detected.model,
        {"resolution": "auto", "framerate": "auto"},
        resolutions=detected.detected_resolutions,
        max_framerates=limits,
    )
    assert auto == (Resolution(1920, 1080), 30)

    still = _resolve_camera_mode(
        detected.model,
        {"resolution": "2592x1944", "framerate": "auto"},
        resolutions=detected.detected_resolutions,
        max_framerates=limits,
    )
    assert still == (Resolution(2592, 1944), 15)


def test_noir_variant_uses_same_video_caps_as_camera_v1():
    from bambucam.camera.models import CAMERA_V1_NOIR
    from bambucam.camera.modes import video_output_capabilities

    detected = _parse_libcamera_output(OV5647)[0]
    modes = video_output_capabilities(CAMERA_V1_NOIR, detected.output_modes)
    assert "2592x1944" not in [mode["resolution"] for mode in modes]
    assert next(mode for mode in modes if mode["resolution"] == "1920x1080")["max_fps"] == 30
