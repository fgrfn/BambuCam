"""Tests for camera selection and mode validation."""

from unittest.mock import MagicMock, patch

import pytest

from bambucam.camera.detector import DetectedCamera
from bambucam.camera.manager import CameraManager
from bambucam.camera.models import CAMERA_USB_GENERIC, CAMERA_V2, Resolution


def _detected(backend: str, index: int = 0) -> DetectedCamera:
    model = CAMERA_V2 if backend == "picamera2" else CAMERA_USB_GENERIC
    return DetectedCamera(
        device=f"device:{index}",
        model=model,
        backend=backend,
        index=index,
        detected_resolutions=model.supported_resolutions,
    )


def test_backend_filter_is_honored():
    manager = CameraManager()
    with patch(
        "bambucam.camera.manager.detect_cameras",
        return_value=[_detected("picamera2"), _detected("v4l2", 1)],
    ):
        selected = manager.detect_and_select(preferred_backend="v4l2")

    assert selected.backend == "v4l2"


def test_unknown_backend_is_rejected():
    manager = CameraManager()
    with patch("bambucam.camera.manager.detect_cameras", return_value=[_detected("v4l2")]):
        with pytest.raises(ValueError, match="Unknown camera backend"):
            manager.detect_and_select(preferred_backend="other")


def test_missing_requested_backend_is_reported():
    manager = CameraManager()
    with patch(
        "bambucam.camera.manager.detect_cameras",
        return_value=[_detected("picamera2")],
    ):
        with pytest.raises(RuntimeError, match="requested backend"):
            manager.detect_and_select(preferred_backend="v4l2")


def test_invalid_mode_is_rejected_before_backend_creation():
    manager = CameraManager()
    detected = _detected("picamera2")

    with pytest.raises(ValueError, match="not supported"):
        manager.setup(
            detected=detected,
            resolution=Resolution(1234, 567),
            framerate=15,
        )


def test_zoom_is_rejected_by_backends_without_scaler_crop():
    manager = CameraManager()
    backend = MagicMock()
    backend.supports_zoom = False
    manager._backend = backend

    with pytest.raises(ValueError, match="Digital zoom is not supported"):
        manager.apply_settings({"zoom": 2.0})
