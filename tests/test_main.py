"""Tests for application startup helpers."""

from bambucam.main import _effective_mjpeg_fps


class TestEffectiveMjpegFps:
    def test_uses_configured_mjpeg_fps(self):
        assert _effective_mjpeg_fps(30, {"fps": 10}) == 10

    def test_caps_mjpeg_fps_to_camera_fps(self):
        assert _effective_mjpeg_fps(15, {"fps": 60}) == 15

    def test_applies_hardware_tier_cap(self):
        assert _effective_mjpeg_fps(60, {"fps": 45}, tier_fps_cap=30) == 30

    def test_invalid_config_falls_back_to_camera_fps(self):
        assert _effective_mjpeg_fps(20, {"fps": "invalid"}) == 20

    def test_never_returns_less_than_one(self):
        assert _effective_mjpeg_fps(30, {"fps": 0}) == 1
