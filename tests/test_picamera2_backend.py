"""Tests for compatibility across picamera2/libcamera releases."""

import logging
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from bambucam.camera.backends.picamera2_backend import Picamera2Backend
from bambucam.camera.models import CAMERA_V3


def _backend() -> Picamera2Backend:
    return Picamera2Backend(CAMERA_V3, "libcamera:0")


def _libcamera_with(controls) -> ModuleType:
    module = ModuleType("libcamera")
    module.controls = controls
    return module


def _control_enums():
    return SimpleNamespace(
        AeExposureModeEnum=SimpleNamespace(
            Normal="normal-value", Short="short-value", Long="long-value"
        ),
        AwbModeEnum=SimpleNamespace(
            Auto="awb-auto-value",
            Daylight="daylight-value",
            Cloudy="cloudy-value",
            Tungsten="tungsten-value",
            Fluorescent="fluorescent-value",
            Indoor="indoor-value",
        ),
        AfModeEnum=SimpleNamespace(Continuous="continuous-value", Manual="manual-value"),
        HdrModeEnum=SimpleNamespace(
            Off="hdr-off-value",
            MultiExposure="multi-exposure-value",
            SingleExposure="single-exposure-value",
        ),
        NoiseReductionModeEnum=SimpleNamespace(
            Off="noise-off-value",
            Minimal="minimal-value",
            Fast="fast-value",
            HighQuality="high-quality-value",
        ),
    )


def _apply_enum_controls(backend: Picamera2Backend) -> None:
    backend.set_exposure_mode("sport")
    backend.set_awb_mode("sunlight")
    backend.set_autofocus(True)
    backend.set_hdr(True)
    backend.set_noise_reduction("high_quality")


EXPECTED_CONTROLS = {
    "AeExposureMode": "short-value",
    "AwbMode": "daylight-value",
    "AfMode": "continuous-value",
    "HdrMode": "multi-exposure-value",
    "NoiseReductionMode": "high-quality-value",
}


def test_enum_controls_use_top_level_enums() -> None:
    controls = _control_enums()

    with patch.dict(sys.modules, {"libcamera": _libcamera_with(controls)}):
        backend = _backend()
        _apply_enum_controls(backend)

    assert backend._pending_controls == EXPECTED_CONTROLS


def test_enum_controls_use_draft_enums() -> None:
    controls = SimpleNamespace(draft=_control_enums())

    with patch.dict(sys.modules, {"libcamera": _libcamera_with(controls)}):
        backend = _backend()
        _apply_enum_controls(backend)

    assert backend._pending_controls == EXPECTED_CONTROLS


def test_missing_control_enums_are_ignored(caplog) -> None:
    controls = SimpleNamespace()

    with patch.dict(sys.modules, {"libcamera": _libcamera_with(controls)}):
        backend = _backend()
        with caplog.at_level(logging.WARNING):
            _apply_enum_controls(backend)

    assert backend._pending_controls == {}
    assert "Exposure modes are not supported" in caplog.text
    assert "AWB modes are not supported" in caplog.text
    assert "Autofocus is not supported" in caplog.text
    assert "HDR is not supported" in caplog.text
    assert "not supported by this libcamera version" in caplog.text


def test_camera_control_capabilities_are_checked(caplog) -> None:
    backend = _backend()
    picam = SimpleNamespace(camera_controls={"Brightness": object()}, set_controls=Mock())
    backend._picam = picam

    with caplog.at_level(logging.WARNING):
        backend._set_control(HdrMode="multi-exposure-value")

    picam.set_controls.assert_not_called()
    assert "Camera does not support control(s): HdrMode" in caplog.text


def test_digital_zoom_uses_centered_scaler_crop() -> None:
    backend = _backend()
    picam = SimpleNamespace(
        camera_controls={
            "ScalerCrop": (
                (0, 0, 64, 64),
                (0, 0, 4608, 2592),
                (0, 0, 4608, 2592),
            )
        },
        set_controls=Mock(),
    )
    backend._picam = picam

    backend.set_zoom(2.0)

    assert backend.supports_zoom is True
    assert backend.max_zoom == 8.0
    picam.set_controls.assert_called_once_with({"ScalerCrop": (1152, 648, 2304, 1296)})


def test_digital_zoom_is_hidden_when_scaler_crop_is_unavailable(caplog) -> None:
    backend = _backend()
    picam = SimpleNamespace(camera_controls={}, set_controls=Mock())
    backend._picam = picam

    with caplog.at_level(logging.WARNING):
        backend.set_zoom(2.0)

    assert backend.supports_zoom is False
    assert backend.max_zoom == 1.0
    picam.set_controls.assert_not_called()
    assert "Digital zoom is not supported" in caplog.text
