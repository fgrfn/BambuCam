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
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_running = False

    # ---------------------------------------------------------------------------
    # Detection & initialisation
    # ---------------------------------------------------------------------------

    def detect_and_select(
        self, preferred_index: int = 0, module_override: str = "auto"
    ) -> DetectedCamera:
        cameras = detect_cameras()
        if not cameras:
            raise RuntimeError(
                "No cameras found. Check connections and run "
                "'rpicam-hello --list-cameras' or 'v4l2-ctl --list-devices'."
            )
        if preferred_index >= len(cameras):
            log.warning("Preferred camera index %d not found, using 0", preferred_index)
            preferred_index = 0
        detected = cameras[preferred_index]

        # Allow config to override the auto-detected model (e.g. v3_noir vs v3)
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
        log.info("Selected camera: %s", self._detected)
        return self._detected

    def setup(
        self,
        detected: Optional[DetectedCamera] = None,
        resolution: Optional[Resolution] = None,
        framerate: int = 30,
        settings: Optional[dict] = None,
        enable_lores: bool = True,
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
        self._start_watchdog()

    def stop(self) -> None:
        self._stop_watchdog()
        if self._backend is not None:
            self._backend.stop()

    def restart(self) -> None:
        """Restart with current settings (e.g., after a resolution change)."""
        self.stop()
        self._backend.configure(self._resolution, self._framerate, **self._extra_settings())
        try:
            self._backend.start()
        except Exception:
            # Watchdog will recover — re-raise so callers know restart failed
            self._start_watchdog()
            raise
        self._start_watchdog()

    # ---------------------------------------------------------------------------
    # Camera watchdog — auto-restarts the camera if it dies unexpectedly
    # ---------------------------------------------------------------------------

    def _extra_settings(self) -> dict:
        """Return self._settings without keys already passed as positional args to configure()."""
        return {k: v for k, v in self._settings.items() if k not in ("resolution", "framerate")}

    def _start_watchdog(self) -> None:
        self._stop_watchdog()
        self._watchdog_running = True
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, daemon=True, name="camera-watchdog"
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
            backoff = min(5 * consecutive_failures, 60)
            time.sleep(backoff)
            if not self._watchdog_running:
                break
            try:
                self._backend.configure(self._resolution, self._framerate, **self._extra_settings())
                self._backend.start()
                consecutive_failures = 0
                log.info("Camera watchdog: restart successful")
            except Exception as e:
                log.error("Camera watchdog: restart failed: %s", e)

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
            new_res = Resolution.from_string(new_settings["resolution"])
            if new_res != self._resolution:
                self._resolution = new_res
                restart_needed = True
        if "framerate" in new_settings:
            new_fps = int(new_settings["framerate"])
            if new_fps != self._framerate:
                self._framerate = new_fps
                restart_needed = True
        # Flips require a full camera restart (applied via Transform at configure time),
        # but only when the value actually changes — not just because the key is present.
        if "vflip" in new_settings and bool(new_settings["vflip"]) != bool(
            self._settings.get("vflip", False)
        ):
            restart_needed = True
        if "hflip" in new_settings and bool(new_settings["hflip"]) != bool(
            self._settings.get("hflip", False)
        ):
            restart_needed = True

        # Merge settings before restart so configure() sees the updated values
        self._settings.update(new_settings)

        if restart_needed and self._backend:
            if self._backend.is_running:
                self.restart()
            # If not running, watchdog will restart with updated settings
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
            if "autofocus" in new_settings:
                b.set_autofocus(bool(new_settings["autofocus"]))
            if "hdr" in new_settings:
                b.set_hdr(bool(new_settings["hdr"]))
            if "noise_reduction" in new_settings:
                b.set_noise_reduction(new_settings["noise_reduction"])

    def set_jpeg_quality(self, value: int) -> None:
        self._settings["jpeg_quality"] = value
        if self._backend is not None:
            self._backend.set_jpeg_quality(value)

    # ---------------------------------------------------------------------------
    # Introspection
    # ---------------------------------------------------------------------------

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
