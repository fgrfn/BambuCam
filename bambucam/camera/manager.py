"""High-level camera manager — wires together detection, backends, and settings."""

import logging
import threading
import time
from collections.abc import Iterator
from typing import Optional

from bambucam.camera.backends.base import CameraBackend
from bambucam.camera.detector import DetectedCamera, detect_cameras
from bambucam.camera.models import CameraModel, Resolution

log = logging.getLogger(__name__)


class CameraManager:
    """Manage camera selection, backend lifecycle, settings, and recovery."""

    def __init__(self):
        self._backend: Optional[CameraBackend] = None
        self._detected: Optional[DetectedCamera] = None
        self._resolution: Optional[Resolution] = None
        self._framerate: int = 30
        self._settings: dict = {}
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_running = False

    def detect_and_select(
        self,
        preferred_index: int = 0,
        module_override: str = "auto",
        preferred_backend: str = "auto",
    ) -> DetectedCamera:
        """Detect cameras and select one after applying an optional backend filter."""
        cameras = detect_cameras()
        if not cameras:
            raise RuntimeError(
                "No cameras found. Check connections and run "
                "'rpicam-hello --list-cameras' or 'v4l2-ctl --list-devices'."
            )

        backend = (preferred_backend or "auto").lower()
        if backend not in {"auto", "picamera2", "v4l2"}:
            raise ValueError(f"Unknown camera backend: {preferred_backend!r}")
        if backend != "auto":
            cameras = [camera for camera in cameras if camera.backend == backend]
            if not cameras:
                raise RuntimeError(f"No cameras found for requested backend {backend!r}")

        if preferred_index < 0 or preferred_index >= len(cameras):
            log.warning("Preferred camera index %d not found, using 0", preferred_index)
            preferred_index = 0
        detected = cameras[preferred_index]

        if module_override and module_override.lower() != "auto":
            from bambucam.camera.models import get_model_by_alias

            override = get_model_by_alias(module_override)
            if override:
                detected = DetectedCamera(
                    device=detected.device,
                    model=override,
                    backend=detected.backend,
                    index=detected.index,
                    detected_resolutions=override.supported_resolutions,
                )
                log.info("Camera model overridden by config: %s", override.name)
            else:
                log.warning(
                    "Unknown camera.module value %r — using auto-detected model",
                    module_override,
                )

        self._detected = detected
        log.info("Selected camera: %s", detected)
        return detected

    def setup(
        self,
        detected: Optional[DetectedCamera] = None,
        resolution: Optional[Resolution] = None,
        framerate: int = 30,
        settings: Optional[dict] = None,
        enable_lores: bool = True,
    ) -> None:
        """Create and configure the backend for the selected camera."""
        detected = detected or self._detected
        if detected is None:
            raise RuntimeError("No camera selected — call detect_and_select() first")

        selected_resolution = resolution or detected.model.max_resolution
        self._validate_mode(selected_resolution, framerate, detected)

        self._detected = detected
        self._resolution = selected_resolution
        self._framerate = int(framerate)
        self._settings = settings or {}

        backend = self._create_backend(detected, enable_lores=enable_lores)
        backend.configure(self._resolution, self._framerate, **self._settings)
        self._backend = backend

    def _create_backend(self, detected: DetectedCamera, enable_lores: bool = True) -> CameraBackend:
        if detected.backend == "picamera2":
            from bambucam.camera.backends.picamera2_backend import Picamera2Backend

            return Picamera2Backend(
                model=detected.model,
                device=detected.device,
                camera_index=detected.index,
                enable_lores=enable_lores,
            )
        if detected.backend == "v4l2":
            from bambucam.camera.backends.v4l2_backend import V4L2Backend

            return V4L2Backend(model=detected.model, device=detected.device)
        raise ValueError(f"Unknown backend: {detected.backend!r}")

    @staticmethod
    def _validate_mode(
        resolution: Resolution,
        framerate: int,
        detected: DetectedCamera,
    ) -> None:
        fps = int(framerate)
        if fps < 1:
            raise ValueError("Framerate must be at least 1 FPS")

        available = detected.detected_resolutions or detected.model.supported_resolutions
        if available and resolution not in available:
            allowed = ", ".join(str(item) for item in available)
            raise ValueError(f"Resolution {resolution} is not supported. Available: {allowed}")

        max_fps = detected.model.resolution_max_framerates.get(
            resolution, detected.model.max_framerate
        )
        if fps > max_fps:
            raise ValueError(f"{resolution} supports at most {max_fps} FPS, requested {fps}")

    def start(self) -> None:
        if self._backend is None:
            raise RuntimeError("Camera not set up — call setup() first")
        self._backend.start()
        self._start_watchdog()

    def stop(self) -> None:
        self._stop_watchdog()
        if self._backend is not None:
            self._backend.stop()

    def restart(self) -> None:
        """Restart the active backend with the current mode and settings."""
        if self._backend is None or self._resolution is None:
            raise RuntimeError("Camera is not configured")
        self.stop()
        self._backend.configure(self._resolution, self._framerate, **self._extra_settings())
        try:
            self._backend.start()
        except Exception:
            self._start_watchdog()
            raise
        self._start_watchdog()

    def _extra_settings(self) -> dict:
        return {
            key: value
            for key, value in self._settings.items()
            if key not in ("resolution", "framerate")
        }

    def _start_watchdog(self) -> None:
        self._stop_watchdog()
        self._watchdog_running = True
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
            name="camera-watchdog",
        )
        self._watchdog_thread.start()

    def _stop_watchdog(self) -> None:
        self._watchdog_running = False
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=2)
            self._watchdog_thread = None

    def _watchdog_loop(self) -> None:
        consecutive_failures = 0
        while self._watchdog_running:
            time.sleep(10)
            if not self._watchdog_running:
                break
            if self._backend is None or self._backend.is_running:
                consecutive_failures = 0
                continue

            consecutive_failures += 1
            log.warning(
                "Camera watchdog: camera not running (attempt %d), restarting…",
                consecutive_failures,
            )
            time.sleep(min(5 * consecutive_failures, 60))
            if not self._watchdog_running:
                break
            try:
                if self._resolution is None:
                    raise RuntimeError("Camera mode is not configured")
                self._backend.configure(
                    self._resolution,
                    self._framerate,
                    **self._extra_settings(),
                )
                self._backend.start()
                consecutive_failures = 0
                log.info("Camera watchdog: restart successful")
            except Exception as exc:
                log.error("Camera watchdog: restart failed: %s", exc)

    def capture_jpeg(self, quality: Optional[int] = None) -> bytes:
        if self._backend is None or not self._backend.is_running:
            raise RuntimeError("Camera is not running")
        return self._backend.capture_jpeg(quality=quality)

    def frame_iterator(self) -> Iterator[bytes]:
        if self._backend is None or not self._backend.is_running:
            raise RuntimeError("Camera is not running")
        return self._backend.frame_iterator()

    def apply_settings(self, new_settings: dict) -> None:
        """Apply camera settings, restarting only when the mode or transform changes."""
        restart_needed = False
        next_resolution = self._resolution
        next_framerate = self._framerate

        if "resolution" in new_settings:
            next_resolution = Resolution.from_string(str(new_settings["resolution"]))
            restart_needed = next_resolution != self._resolution
        if "framerate" in new_settings:
            next_framerate = int(new_settings["framerate"])
            restart_needed = restart_needed or next_framerate != self._framerate

        if self._detected is not None and next_resolution is not None:
            self._validate_mode(next_resolution, next_framerate, self._detected)

        if "zoom" in new_settings and self._backend is not None and not self._backend.supports_zoom:
            raise ValueError("Digital zoom is not supported by the active camera backend")

        if "vflip" in new_settings and bool(new_settings["vflip"]) != bool(
            self._settings.get("vflip", False)
        ):
            restart_needed = True
        if "hflip" in new_settings and bool(new_settings["hflip"]) != bool(
            self._settings.get("hflip", False)
        ):
            restart_needed = True

        self._resolution = next_resolution
        self._framerate = next_framerate
        self._settings.update(new_settings)

        if restart_needed and self._backend:
            if self._backend.is_running:
                self.restart()
            return

        if self._backend and self._backend.is_running:
            backend = self._backend
            setters = {
                "brightness": lambda value: backend.set_brightness(float(value)),
                "contrast": lambda value: backend.set_contrast(float(value)),
                "saturation": lambda value: backend.set_saturation(float(value)),
                "sharpness": lambda value: backend.set_sharpness(float(value)),
                "zoom": lambda value: backend.set_zoom(float(value)),
                "exposure_mode": backend.set_exposure_mode,
                "awb_mode": backend.set_awb_mode,
                "iso": lambda value: backend.set_iso(int(value)),
                "autofocus": lambda value: backend.set_autofocus(bool(value)),
                "hdr": lambda value: backend.set_hdr(bool(value)),
                "noise_reduction": backend.set_noise_reduction,
            }
            for key, setter in setters.items():
                if key in new_settings:
                    setter(new_settings[key])

    def set_jpeg_quality(self, value: int) -> None:
        self._settings["jpeg_quality"] = value
        if self._backend is not None:
            self._backend.set_jpeg_quality(value)

    @property
    def backend(self) -> Optional[CameraBackend]:
        return self._backend

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
        return self._backend.get_v4l2_device() if self._backend is not None else None

    def status(self) -> dict:
        return {
            "running": self.is_running,
            "model": self.model.name if self.model else None,
            "sensor": self.model.sensor if self.model else None,
            "backend": self._detected.backend if self._detected else None,
            "device": self._detected.device if self._detected else None,
            "resolution": str(self._resolution) if self._resolution else None,
            "framerate": self._framerate,
            "available_resolutions": (
                [str(item) for item in self._detected.detected_resolutions]
                if self._detected
                else []
            ),
            "has_autofocus": self.model.has_autofocus if self.model else False,
            "has_hdr": self.model.has_hdr if self.model else False,
            "has_zoom": self._backend.supports_zoom if self._backend else False,
            "max_zoom": self._backend.max_zoom if self._backend else 1.0,
            "zoom": float(self._settings.get("zoom", 1.0)),
            "is_noir": self.model.is_noir if self.model else False,
            "has_global_shutter": self.model.has_global_shutter if self.model else False,
        }
