"""Tests for model-aware camera profiles."""

from copy import deepcopy

import pytest

from bambucam.camera.models import CameraModel, Resolution
from bambucam.camera.profiles import CameraProfileService
from bambucam.config import DEFAULTS


class FakeConfig:
    def __init__(self):
        self.data = deepcopy(DEFAULTS)
        self.saved = 0

    def get(self, *keys, default=None):
        node = self.data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def update_section(self, section, values):
        current = self.data.setdefault(section, {})
        for key, value in values.items():
            if isinstance(value, dict) and isinstance(current.get(key), dict):
                current[key].update(value)
            else:
                current[key] = value

    def set(self, *keys, value):
        node = self.data
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value

    def save(self):
        self.saved += 1

    def as_dict(self):
        return deepcopy(self.data)

    def replace(self, data):
        self.data = deepcopy(data)


class FakeCamera:
    def __init__(self):
        self.model = CameraModel(
            id="test",
            name="Test Camera",
            sensor="TEST",
            megapixels=8,
            max_resolution=Resolution(3840, 2160),
            max_framerate=60,
            supported_resolutions=[
                Resolution(640, 480),
                Resolution(1280, 720),
                Resolution(1920, 1080),
                Resolution(3840, 2160),
            ],
            supported_framerates=[10, 15, 30, 60],
            resolution_max_framerates={
                Resolution(640, 480): 60,
                Resolution(1280, 720): 60,
                Resolution(1920, 1080): 30,
                Resolution(3840, 2160): 12,
            },
        )
        self.settings = None
        self.quality = None

    def status(self):
        return {
            "resolution": "1920x1080",
            "framerate": 15,
            "available_resolutions": [
                "640x480",
                "1280x720",
                "1920x1080",
                "3840x2160",
            ],
        }

    def apply_settings(self, settings):
        self.settings = settings

    def set_jpeg_quality(self, quality):
        self.quality = quality


class FakeMJPEG:
    def __init__(self):
        self.fps = None

    def update_fps(self, fps):
        self.fps = fps


class FakeRTSP:
    def __init__(self):
        self.settings = None

    def update_settings(self, **settings):
        self.settings = settings


def service():
    return CameraProfileService(FakeConfig(), FakeCamera(), FakeMJPEG(), FakeRTSP())


def test_quality_profile_uses_largest_mode_and_caps_fps():
    profile = service().resolve("quality")
    assert profile["resolution"] == "3840x2160"
    assert profile["framerate"] == 12
    assert profile["bitrate_kbps"] == 6000


def test_balanced_profile_prefers_1080p():
    profile = service().resolve("balanced")
    assert profile["resolution"] == "1920x1080"
    assert profile["framerate"] == 15


def test_profile_apply_updates_runtime_and_persists():
    profiles = service()
    result = profiles.apply("low_latency")

    assert profiles._camera.settings["resolution"] == "1280x720"
    assert profiles._camera.settings["framerate"] == 30
    assert profiles._camera.quality == 78
    assert profiles._mjpeg.fps == 30
    assert profiles._rtsp.settings["bitrate_kbps"] == 3000
    assert profiles._config.data["camera"]["active_profile"] == "low_latency"
    assert profiles._config.saved == 1
    assert result["name"] == "low_latency"


def test_unknown_profile_is_rejected():
    try:
        service().resolve("unknown")
    except ValueError as exc:
        assert "Unknown camera profile" in str(exc)
    else:
        raise AssertionError("Unknown profile was accepted")


def test_profile_save_failure_restores_config_and_runtime():
    profiles = service()
    original = profiles._config.as_dict()

    def fail_save():
        raise OSError("disk full")

    profiles._config.save = fail_save
    with pytest.raises(OSError, match="disk full"):
        profiles.apply("low_latency")

    assert profiles._config.data == original
    assert profiles._camera.settings["resolution"] == "1920x1080"
    assert profiles._camera.settings["framerate"] == 15
    assert profiles._rtsp.settings["bitrate_kbps"] == 2000
