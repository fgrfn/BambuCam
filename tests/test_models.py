"""Tests for camera model definitions."""

import pytest

from bambucam.camera.models import (
    CAMERA_HQ,
    CAMERA_USB_GENERIC,
    CAMERA_V1,
    CAMERA_V2,
    CAMERA_V3,
    KNOWN_MODELS,
    Resolution,
    get_model_by_id,
    get_model_by_sensor,
)


class TestResolution:
    def test_str(self):
        assert str(Resolution(1920, 1080)) == "1920x1080"

    def test_from_string(self):
        r = Resolution.from_string("1920x1080")
        assert r.width == 1920
        assert r.height == 1080

    def test_from_string_case_insensitive(self):
        r = Resolution.from_string("1920X1080")
        assert r == Resolution(1920, 1080)

    def test_from_string_invalid(self):
        with pytest.raises(ValueError):
            Resolution.from_string("1920")

    def test_as_tuple(self):
        assert Resolution(640, 480).as_tuple() == (640, 480)

    def test_frozen(self):
        r = Resolution(640, 480)
        with pytest.raises(Exception):
            r.width = 1280


class TestCameraModels:
    def test_v1_sensor(self):
        assert CAMERA_V1.sensor == "OV5647"

    def test_v2_has_no_autofocus(self):
        assert not CAMERA_V2.has_autofocus

    def test_v3_has_autofocus_and_hdr(self):
        assert CAMERA_V3.has_autofocus
        assert CAMERA_V3.has_hdr

    def test_hq_max_resolution(self):
        assert CAMERA_HQ.max_resolution == Resolution(4056, 3040)

    def test_all_models_have_supported_resolutions(self):
        for model in KNOWN_MODELS:
            assert len(model.supported_resolutions) > 0, f"{model.name} has no resolutions"

    def test_all_models_have_supported_framerates(self):
        for model in KNOWN_MODELS:
            assert len(model.supported_framerates) > 0, f"{model.name} has no framerates"

    def test_get_model_by_id(self):
        m = get_model_by_id("imx219_v2")
        assert m is CAMERA_V2

    def test_get_model_by_id_unknown(self):
        assert get_model_by_id("nonexistent") is None

    def test_get_model_by_sensor(self):
        assert get_model_by_sensor("imx219") is CAMERA_V2
        assert get_model_by_sensor("IMX219") is CAMERA_V2

    def test_get_model_by_sensor_unknown(self):
        assert get_model_by_sensor("xyz999") is None

    def test_best_framerate_for(self):
        assert CAMERA_V2.best_framerate_for(25) == 25
        assert CAMERA_V2.best_framerate_for(20) == 15
        assert CAMERA_V2.best_framerate_for(100) == 90

    def test_usb_generic_no_features(self):
        assert not CAMERA_USB_GENERIC.has_autofocus
        assert not CAMERA_USB_GENERIC.has_hdr
        assert not CAMERA_USB_GENERIC.is_noir
