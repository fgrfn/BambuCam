"""V4L2 backend for USB webcams and other Video4Linux2 cameras."""

import logging
import subprocess
import threading
from typing import Optional

from bambucam.camera.backends.base import CameraBackend
from bambucam.camera.models import CameraModel, Resolution

log = logging.getLogger(__name__)


class V4L2Backend(CameraBackend):
    """Capture USB-camera frames through OpenCV and configure controls via v4l2-ctl."""

    def __init__(self, model: CameraModel, device: str):
        super().__init__(model, device)
        self._resolution: Optional[Resolution] = None
        self._framerate: int = 30
        self._jpeg_quality: int = 85
        self._lock = threading.Lock()
        self._cap = None
        self._cv2 = None
        self._initial_settings: dict = {}

    def configure(self, resolution: Resolution, framerate: int, **kwargs) -> None:
        self._resolution = resolution
        self._framerate = int(framerate)
        self._initial_settings = dict(kwargs)

    def start(self) -> None:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError(
                "opencv-python is not installed. Run: pip install opencv-python-headless"
            ) from exc

        self._cv2 = cv2
        resolution = self._resolution or self.model.max_resolution
        log.info(
            "Starting V4L2 backend: %s @ %s %dfps",
            self.device,
            resolution,
            self._framerate,
        )

        capture = cv2.VideoCapture(self.device)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(f"Cannot open V4L2 device: {self.device}")

        # Prefer camera-side MJPEG where supported. It substantially reduces USB
        # bandwidth and CPU usage compared with uncompressed YUYV.
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, resolution.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution.height)
        capture.set(cv2.CAP_PROP_FPS, self._framerate)

        self._cap = capture
        self._running = True
        self._apply_initial_settings()

        actual_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = capture.get(cv2.CAP_PROP_FPS)
        if actual_width != resolution.width or actual_height != resolution.height:
            log.warning(
                "V4L2 camera negotiated %dx%d instead of requested %s",
                actual_width,
                actual_height,
                resolution,
            )
        log.info(
            "V4L2 backend started: %s (%dx%d @ %.1f fps)",
            self.device,
            actual_width,
            actual_height,
            actual_fps,
        )

    def _apply_initial_settings(self) -> None:
        setters = {
            "brightness": lambda value: self.set_brightness(float(value)),
            "contrast": lambda value: self.set_contrast(float(value)),
            "saturation": lambda value: self.set_saturation(float(value)),
            "sharpness": lambda value: self.set_sharpness(float(value)),
            "vflip": lambda value: self.set_vflip(bool(value)),
            "hflip": lambda value: self.set_hflip(bool(value)),
            "jpeg_quality": lambda value: self.set_jpeg_quality(int(value)),
        }
        for key, setter in setters.items():
            if key not in self._initial_settings:
                continue
            try:
                setter(self._initial_settings[key])
            except Exception as exc:
                log.warning("Failed to apply initial V4L2 setting %s: %s", key, exc)

    def stop(self) -> None:
        self._running = False
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None
        log.info("V4L2 backend stopped: %s", self.device)

    def capture_jpeg(self, quality: Optional[int] = None) -> bytes:
        if self._cap is None or self._cv2 is None:
            raise RuntimeError("Camera not started")

        jpeg_quality = self._jpeg_quality if quality is None else int(quality)
        jpeg_quality = max(1, min(100, jpeg_quality))
        with self._lock:
            success, frame = self._cap.read()
        if not success:
            self._running = False
            raise RuntimeError("Failed to capture frame from V4L2 device")

        encoded, buffer = self._cv2.imencode(
            ".jpg",
            frame,
            [self._cv2.IMWRITE_JPEG_QUALITY, jpeg_quality],
        )
        if not encoded:
            raise RuntimeError("Failed to encode JPEG")
        return buffer.tobytes()

    def set_jpeg_quality(self, value: int) -> None:
        self._jpeg_quality = max(1, min(100, int(value)))

    def set_brightness(self, value: float) -> None:
        self._v4l2_set("brightness", int(max(-1.0, min(1.0, value)) * 128 + 128))

    def set_contrast(self, value: float) -> None:
        self._v4l2_set("contrast", int(max(0.0, min(2.0, value)) * 128))

    def set_saturation(self, value: float) -> None:
        self._v4l2_set("saturation", int(max(0.0, min(2.0, value)) * 128))

    def set_sharpness(self, value: float) -> None:
        self._v4l2_set("sharpness", int(max(0.0, min(2.0, value)) * 128))

    def set_vflip(self, enabled: bool) -> None:
        self._v4l2_set("vertical_flip", int(enabled))

    def set_hflip(self, enabled: bool) -> None:
        self._v4l2_set("horizontal_flip", int(enabled))

    def _v4l2_set(self, control: str, value: int) -> None:
        try:
            result = subprocess.run(
                [
                    "v4l2-ctl",
                    "--device",
                    self.device,
                    "--set-ctrl",
                    f"{control}={value}",
                ],
                check=False,
                timeout=3,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                message = result.stderr.strip() or result.stdout.strip() or "unsupported control"
                log.debug("V4L2 control %s was not applied: %s", control, message)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            log.warning("v4l2-ctl failed for %s: %s", control, exc)

    def get_v4l2_device(self) -> str:
        return self.device
