"""High-level camera manager — wires together detection, backends, and settings."""

import logging
from typing import Iterator, Optional

from bambucam.camera.backends.base import CameraBackend
from bambucam.camera.detector import DetectedCamera, detect_cameras
from bambucam.camera.models import CameraModel, Resolution

log = logging.getLogger(__name__)


class CameraManager:
    """
    Manages the active camera backend lifecycle.
    Instantiated once at startup; shared across the streaming and web layers.
    """

    def __init__(self):
        self._backend: Optional[CameraBackend] = None
        self._detected: Optional[DetectedCamera] = None
        self._resolution: Optional[Resolution] = None
        self._framerate: int = 30
        self._settings: dict = {}

    # ---------------------------------------------------------------------------
    # Detection & initialisation
    # ---------------------------------------------------------------------------

    def detect_and_select(self, preferred_index: int = 0) -> DetectedCamera:
        cameras = detect_cameras()
        if not cameras:
            raise RuntimeError(
                "No cameras found. Check connections and run "
                "'libcamera-hello --list-cameras' or 'v4l2-ctl --list-devices'."
            )
        if preferred_index >= len(cameras):
            log.warning(
                "Preferred camera index %d not found, using 0", preferred_index
            )
            preferred_index = 0
        self._detected = cameras[preferred_index]
        log.info("Selected camera: %s", self._detected)
        return self._detected

    def setup(
        self,
        detected: Optional[DetectedCamera] = None,
        resolution: Optional[Resolution] = None,
        framerate: int = 30,
        settings: Optional[dict] = None,
    ) -> None:
        """Create and configure the backend for the given camera."""
        if detected is None:
            detected = self._detected
        if detected is None:
            raise RuntimeError("No camera selected — call detect_and_select() first")

        self._detected = detected
        self._resolution = resolution or detected.model.supported_resolutions[1]
        self._framerate = framerate
        self._settings = settings or {}

        backend = self._create_backend(detected)
        backend.configure(self._resolution, self._framerate, **self._settings)
        self._backend = backend

    def _create_backend(self, detected: DetectedCamera) -> CameraBackend:
        if detected.backend == "picamera2":
            from bambucam.camera.backends.picamera2_backend import Picamera2Backend
            return Picamera2Backend(
                model=detected.model,
                device=detected.device,
                camera_index=detected.index,
            )
        elif detected.backend == "v4l2":
            from bambucam.camera.backends.v4l2_backend import V4L2Backend
            return V4L2Backend(model=detected.model, device=detected.device)
        else:
            raise ValueError(f"Unknown backend: {detected.backend!r}")

    # ---------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------

    def start(self) -> None:
        if self._backend is None:
            raise RuntimeError("Camera not set up — call setup() first")
        self._backend.start()

    def stop(self) -> None:
        if self._backend is not None:
            self._backend.stop()

    def restart(self) -> None:
        """Restart with current settings (e.g., after a resolution change)."""
        self.stop()
        self._backend.configure(self._resolution, self._framerate, **self._settings)
        self._backend.start()

    # ---------------------------------------------------------------------------
    # Frame access
    # ---------------------------------------------------------------------------

    def capture_jpeg(self) -> bytes:
        if self._backend is None or not self._backend.is_running:
            raise RuntimeError("Camera is not running")
        return self._backend.capture_jpeg()

    def frame_iterator(self) -> Iterator[bytes]:
        if self._backend is None or not self._backend.is_running:
            raise RuntimeError("Camera is not running")
        return self._backend.frame_iterator()

    # ---------------------------------------------------------------------------
    # Settings
    # ---------------------------------------------------------------------------

    def apply_settings(self, new_settings: dict) -> None:
        """
        Apply a dict of settings to the running backend.
        Keys: resolution, framerate, brightness, contrast, saturation,
              sharpness, exposure_mode, awb_mode, vflip, hflip,
              autofocus, hdr
        """
        restart_needed = False

        if "resolution" in new_settings:
            self._resolution = Resolution.from_string(new_settings["resolution"])
            restart_needed = True
        if "framerate" in new_settings:
            self._framerate = int(new_settings["framerate"])
            restart_needed = True

        if restart_needed and self._backend and self._backend.is_running:
            self._backend.configure(self._resolution, self._framerate)
            self.restart()
            return

        if self._backend and self._backend.is_running:
            b = self._backend
            if "brightness" in new_settings:
                b.set_brightness(float(new_settings["brightness"]))
            if "contrast" in new_settings:
                b.set_contrast(float(new_settings["contrast"]))
            if "saturation" in new_settings:
                b.set_saturation(float(new_settings["saturation"]))
            if "sharpness" in new_settings:
                b.set_sharpness(float(new_settings["sharpness"]))
            if "exposure_mode" in new_settings:
                b.set_exposure_mode(new_settings["exposure_mode"])
            if "awb_mode" in new_settings:
                b.set_awb_mode(new_settings["awb_mode"])
            if "vflip" in new_settings:
                b.set_vflip(bool(new_settings["vflip"]))
            if "hflip" in new_settings:
                b.set_hflip(bool(new_settings["hflip"]))
            if "autofocus" in new_settings:
                b.set_autofocus(bool(new_settings["autofocus"]))
            if "hdr" in new_settings:
                b.set_hdr(bool(new_settings["hdr"]))

        self._settings.update(new_settings)

    # ---------------------------------------------------------------------------
    # Introspection
    # ---------------------------------------------------------------------------

    @property
    def model(self) -> Optional[CameraModel]:
        return self._detected.model if self._detected else None

    @property
    def is_running(self) -> bool:
        return self._backend is not None and self._backend.is_running

    @property
    def current_resolution(self) -> Optional[Resolution]:
        return self._resolution

    @property
    def current_framerate(self) -> int:
        return self._framerate

    @property
    def v4l2_device(self) -> Optional[str]:
        if self._backend is not None:
            return self._backend.get_v4l2_device()
        return None

    def status(self) -> dict:
        return {
            "running": self.is_running,
            "model": self.model.name if self.model else None,
            "sensor": self.model.sensor if self.model else None,
            "backend": self._detected.backend if self._detected else None,
            "device": self._detected.device if self._detected else None,
            "resolution": str(self._resolution) if self._resolution else None,
            "framerate": self._framerate,
            "has_autofocus": self.model.has_autofocus if self.model else False,
            "has_hdr": self.model.has_hdr if self.model else False,
            "is_noir": self.model.is_noir if self.model else False,
            "has_global_shutter": self.model.has_global_shutter if self.model else False,
        }
