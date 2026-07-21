"""Tests for compatibility across picamera2/libcamera releases."""

import logging
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from bambucam.camera.backends.picamera2_backend import Picamera2Backend
from bambucam.camera.models import CAMERA_V2


def _backend() -> Picamera2Backend:
    return Picamera2Backend(CAMERA_V2, "libcamera:0")


def _libcamera_with(controls) -> ModuleType:
    module = ModuleType("libcamera")
    module.controls = controls
    return module


def _noise_reduction_enum():
    return SimpleNamespace(
        Off="off-value",
        Minimal="minimal-value",
        Fast="fast-value",
        HighQuality="high-quality-value",
    )


def test_noise_reduction_uses_top_level_enum() -> None:
    controls = SimpleNamespace(NoiseReductionModeEnum=_noise_reduction_enum())

    with patch.dict(sys.modules, {"libcamera": _libcamera_with(controls)}):
        backend = _backend()
        backend.set_noise_reduction("fast")

    assert backend._pending_controls == {"NoiseReductionMode": "fast-value"}


def test_noise_reduction_uses_draft_enum() -> None:
    controls = SimpleNamespace(
        draft=SimpleNamespace(NoiseReductionModeEnum=_noise_reduction_enum())
    )

    with patch.dict(sys.modules, {"libcamera": _libcamera_with(controls)}):
        backend = _backend()
        backend.set_noise_reduction("high_quality")

    assert backend._pending_controls == {"NoiseReductionMode": "high-quality-value"}


def test_missing_noise_reduction_enum_is_ignored(caplog) -> None:
    controls = SimpleNamespace()

    with patch.dict(sys.modules, {"libcamera": _libcamera_with(controls)}):
        backend = _backend()
        with caplog.at_level(logging.WARNING):
            backend.set_noise_reduction("fast")

    assert backend._pending_controls == {}
    assert "not supported by this libcamera version" in caplog.text
