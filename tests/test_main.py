"""Tests for application startup helpers."""

import pytest

from bambucam.camera.models import CAMERA_V2, Resolution
from bambucam.main import _effective_mjpeg_fps, _resolve_auto_bool, _resolve_camera_mode
from bambucam.system_info import hardware_recommendations


class TestEffectiveMjpegFps:
    def test_uses_configured_mjpeg_fps(self):
        assert _effective_mjpeg_fps(30, {"fps": 10}) == 10

    def test_caps_mjpeg_fps_to_camera_fps(self):
        assert _effective_mjpeg_fps(15, {"fps": 60}) == 15

    def test_applies_hardware_tier_cap(self):
        assert _effective_mjpeg_fps(60, {"fps": 45}, tier_fps_cap=30) == 30

    def test_without_hardware_cap_preserves_selected_fps(self):
        assert _effective_mjpeg_fps(30, {"fps": 30}, tier_fps_cap=None) == 30

    def test_invalid_config_falls_back_to_camera_fps(self):
        assert _effective_mjpeg_fps(20, {"fps": "invalid"}) == 20

    def test_never_returns_less_than_one(self):
        assert _effective_mjpeg_fps(30, {"fps": 0}) == 1


def test_auto_rtsp_switch_uses_hardware_default_but_preserves_explicit_choice():
    assert _resolve_auto_bool("auto", False) is False
    assert _resolve_auto_bool("auto", True) is True
    assert _resolve_auto_bool(True, False) is True
    assert _resolve_auto_bool(False, True) is False


def test_pi_zero_recommendations_are_conservative():
    assert hardware_recommendations(1) == {
        "rtsp_enabled": False,
        "recommended_profile": "low_power",
    }
    assert hardware_recommendations(2)["rtsp_enabled"] is True
    assert hardware_recommendations(2)["recommended_profile"] == "balanced"


class TestResolveCameraMode:
    def test_auto_mode_selects_supported_values(self):
        resolution, fps = _resolve_camera_mode(
            CAMERA_V2,
            {"resolution": "auto", "framerate": "auto"},
            tier_fps_cap=30,
        )

        assert resolution in CAMERA_V2.supported_resolutions
        assert 1 <= fps <= 30

    def test_explicit_fps_is_capped_to_hardware(self):
        resolution = Resolution(1920, 1080)
        selected_resolution, fps = _resolve_camera_mode(
            CAMERA_V2,
            {"resolution": str(resolution), "framerate": 120},
            tier_fps_cap=15,
        )

        assert selected_resolution == resolution
        assert fps == 15

    def test_rejects_unsupported_resolution(self):
        with pytest.raises(ValueError, match="not supported"):
            _resolve_camera_mode(
                CAMERA_V2,
                {"resolution": "1234x567", "framerate": 15},
            )

    def test_rejects_invalid_framerate(self):
        with pytest.raises(ValueError, match="Invalid camera framerate"):
            _resolve_camera_mode(
                CAMERA_V2,
                {"resolution": "1920x1080", "framerate": "fast"},
            )
