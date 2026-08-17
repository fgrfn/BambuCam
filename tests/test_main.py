"""Tests for application startup helpers."""

from unittest.mock import MagicMock

import pytest

from bambucam.camera.models import CAMERA_V2, Resolution
from bambucam.main import (
    RTSP_BITRATE_CEILING_KBPS,
    _clamp_rtsp_bitrate,
    _effective_mjpeg_fps,
    _mjpeg_bitrate_kbps,
    _mjpeg_source,
    _resolve_auto_bool,
    _resolve_camera_mode,
    _resolve_hw_encoder,
    _rtsp_encode_size,
)
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


class TestClampRtspBitrate:
    def test_value_within_tier_limit_is_unchanged(self):
        assert _clamp_rtsp_bitrate(2000, 1) == 2000
        assert _clamp_rtsp_bitrate(8000, 2) == 8000

    def test_value_above_tier_limit_is_clamped(self):
        assert _clamp_rtsp_bitrate(100000, 1) == RTSP_BITRATE_CEILING_KBPS[1]
        assert _clamp_rtsp_bitrate(20000, 2) == RTSP_BITRATE_CEILING_KBPS[2]

    def test_top_tier_is_capped_too(self):
        assert _clamp_rtsp_bitrate(100000, 3) == 20000

    def test_unknown_tier_falls_back_to_sane_ceiling(self):
        assert _clamp_rtsp_bitrate(100000, 99) == 20000
        assert _clamp_rtsp_bitrate(5000, None) == 5000

    def test_invalid_value_falls_back_to_default(self):
        assert _clamp_rtsp_bitrate("not-a-number", 1) == 2000

    def test_clamping_is_logged(self):
        logger = MagicMock()
        _clamp_rtsp_bitrate(100000, 1, logger)
        assert logger.warning.called

    def test_allowed_value_is_not_logged(self):
        logger = MagicMock()
        _clamp_rtsp_bitrate(2000, 1, logger)
        logger.warning.assert_not_called()


class TestResolveHwEncoder:
    def test_auto_defers_to_runtime_detection(self):
        assert _resolve_hw_encoder("auto") is None
        assert _resolve_hw_encoder(None) is None
        assert _resolve_hw_encoder("") is None

    def test_explicit_choice_is_honoured(self):
        assert _resolve_hw_encoder(True) is True
        assert _resolve_hw_encoder(False) is False


class TestRtspEncodeSize:
    def test_tier_one_gets_a_small_encode_stream(self):
        assert _rtsp_encode_size(Resolution(1920, 1080), 1) == (640, 360)

    def test_tier_three_allows_full_hd(self):
        assert _rtsp_encode_size(Resolution(1920, 1080), 3) == (1920, 1080)

    def test_aspect_ratio_is_preserved(self):
        # 4:3 sensor must not be stretched into the 16:9 ceiling.
        assert _rtsp_encode_size(Resolution(2592, 1944), 2) == (960, 720)

    def test_small_camera_modes_are_never_upscaled(self):
        assert _rtsp_encode_size(Resolution(640, 480), 3) == (640, 480)

    def test_unknown_tier_falls_back(self):
        assert _rtsp_encode_size(Resolution(1920, 1080), 99) == (1280, 720)


class TestMjpegBitrate:
    def test_scales_with_size_and_framerate(self):
        small = _mjpeg_bitrate_kbps((640, 360), 15, 85)
        large = _mjpeg_bitrate_kbps((1280, 720), 15, 85)
        faster = _mjpeg_bitrate_kbps((640, 360), 30, 85)

        assert large > small
        assert faster > small

    def test_higher_quality_costs_more_bitrate(self):
        assert _mjpeg_bitrate_kbps((1280, 720), 15, 95) > _mjpeg_bitrate_kbps((1280, 720), 15, 50)

    def test_stays_above_a_usable_floor(self):
        assert _mjpeg_bitrate_kbps((64, 48), 1, 1) >= 500

    def test_out_of_range_quality_is_clamped(self):
        assert _mjpeg_bitrate_kbps((640, 360), 15, 500) == _mjpeg_bitrate_kbps((640, 360), 15, 100)
        assert _mjpeg_bitrate_kbps((640, 360), 15, -5) == _mjpeg_bitrate_kbps((640, 360), 15, 1)


class TestMjpegSource:
    def test_without_a_backend_it_falls_back_to_still_capture(self):
        camera = MagicMock()
        capture, on_resume, on_pause = _mjpeg_source(camera, None, 4000)

        assert capture is camera.capture_jpeg
        assert on_resume is None
        assert on_pause is None

    def test_encoder_frames_are_preferred(self):
        camera = MagicMock()
        backend = MagicMock()
        backend.is_mjpeg_streaming = True
        backend.latest_jpeg.return_value = b"encoded"

        capture, _on_resume, _on_pause = _mjpeg_source(camera, backend, 4000)

        assert capture() == b"encoded"
        camera.capture_jpeg.assert_not_called()

    def test_still_capture_covers_an_encoder_that_never_started(self):
        camera = MagicMock()
        camera.capture_jpeg.return_value = b"still"
        backend = MagicMock()
        backend.is_mjpeg_streaming = False

        capture, _on_resume, _on_pause = _mjpeg_source(camera, backend, 4000)

        assert capture() == b"still"
        backend.latest_jpeg.assert_not_called()

    def test_hooks_drive_the_encoder_with_the_configured_bitrate(self):
        backend = MagicMock()
        _capture, on_resume, on_pause = _mjpeg_source(MagicMock(), backend, 7500)

        on_resume()
        backend.start_mjpeg_stream.assert_called_once_with(7500)
        assert on_pause is backend.stop_mjpeg_stream
