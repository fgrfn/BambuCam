"""
V4L2 backend for USB webcams and other V4L2-compatible cameras.
Uses subprocess calls to v4l2-ctl for configuration and
OpenCV / subprocess ffmpeg for frame capture.
"""

import io
import logging
import subprocess
import threading
import time
from typing import Optional

from bambucam.camera.backends.base import CameraBackend
from bambucam.camera.models import CameraModel, Resolution

log = logging.getLogger(__name__)


class V4L2Backend(CameraBackend):
    """Backend for USB webcams via Video4Linux2."""

    def __init__(self, model: CameraModel, device: str):
        super().__init__(model, device)
        self._resolution: Optional[Resolution] = None
        self._framerate: int = 30
        self._lock = threading.Lock()
        self._cap = None   # OpenCV VideoCapture

    def configure(self, resolution: Resolution, framerate: int, **kwargs) -> None:
        self._resolution = resolution
        self._framerate = framerate

    def start(self) -> None:
        try:
            import cv2
            self._cv2 = cv2
        except ImportError:
            raise RuntimeError(
                "opencv-python is not installed. Run: pip install opencv-python-headless"
            )

        res = self._resolution or self.model.max_resolution
        log.info("Starting V4L2 backend: %s @ %s %dfps", self.device, res, self._framerate)

        self._cap = self._cv2.VideoCapture(self.device)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open V4L2 device: {self.device}")

        self._cap.set(self._cv2.CAP_PROP_FRAME_WIDTH, res.width)
        self._cap.set(self._cv2.CAP_PROP_FRAME_HEIGHT, res.height)
        self._cap.set(self._cv2.CAP_PROP_FPS, self._framerate)

        self._running = True
        log.info("V4L2 backend started: %s", self.device)

    def stop(self) -> None:
        self._running = False
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        log.info("V4L2 backend stopped: %s", self.device)

    def capture_jpeg(self) -> bytes:
        if self._cap is None:
            raise RuntimeError("Camera not started")
        with self._lock:
            ret, frame = self._cap.read()
        if not ret:
            raise RuntimeError("Failed to capture frame from V4L2 device")
        ok, buf = self._cv2.imencode(".jpg", frame, [self._cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise RuntimeError("Failed to encode JPEG")
        return buf.tobytes()

    def set_brightness(self, value: float) -> None:
        self._v4l2_set("brightness", int(value * 128 + 128))

    def set_contrast(self, value: float) -> None:
        self._v4l2_set("contrast", int(value * 128))

    def set_saturation(self, value: float) -> None:
        self._v4l2_set("saturation", int(value * 128))

    def set_vflip(self, enabled: bool) -> None:
        self._v4l2_set("vertical_flip", int(enabled))

    def set_hflip(self, enabled: bool) -> None:
        self._v4l2_set("horizontal_flip", int(enabled))

    def _v4l2_set(self, control: str, value: int) -> None:
        try:
            subprocess.run(
                ["v4l2-ctl", "--device", self.device, "--set-ctrl", f"{control}={value}"],
                check=False, timeout=3, capture_output=True,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            log.warning("v4l2-ctl failed for %s: %s", control, e)

    def get_v4l2_device(self) -> str:
        return self.device
