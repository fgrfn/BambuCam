"""
picamera2 backend — uses the libcamera stack (Raspberry Pi OS Bullseye+).
Requires: sudo apt install python3-picamera2
"""

import io
import logging
import threading
from typing import Optional

from bambucam.camera.backends.base import CameraBackend
from bambucam.camera.models import CameraModel, Resolution

log = logging.getLogger(__name__)


class Picamera2Backend(CameraBackend):
    """Camera backend using the picamera2 / libcamera stack."""

    EXPOSURE_MODES = {
        "auto": None,
        "sport": "short",
        "night": "long",
        "manual": "manual",
    }

    AWB_MODES = {
        "auto": "Auto",
        "sunlight": "Daylight",
        "cloudy": "Cloudy",
        "shade": "Shade",
        "tungsten": "Tungsten",
        "fluorescent": "Fluorescent",
        "incandescent": "Incandescent",
        "flash": "Flash",
        "horizon": "Horizon",
        "greyworld": "GreyWorld",
    }

    def __init__(self, model: CameraModel, device: str, camera_index: int = 0):
        super().__init__(model, device)
        self._camera_index = camera_index
        self._picam = None
        self._lock = threading.Lock()
        self._resolution: Optional[Resolution] = None
        self._framerate: int = 30
        self._pending_controls: dict = {}

    def configure(self, resolution: Resolution, framerate: int, **kwargs) -> None:
        self._resolution = resolution
        self._framerate = framerate
        if kwargs.get("vflip"):
            self._pending_controls["vflip"] = kwargs["vflip"]
        if kwargs.get("hflip"):
            self._pending_controls["hflip"] = kwargs["hflip"]

    def start(self) -> None:
        try:
            from picamera2 import Picamera2
        except ImportError:
            raise RuntimeError(
                "picamera2 is not installed. " "Run: sudo apt install python3-picamera2"
            )

        res = self._resolution or self.model.max_resolution
        log.info(
            "Starting picamera2 backend: %s @ %s %dfps",
            self.model.name,
            res,
            self._framerate,
        )

        self._picam = Picamera2(self._camera_index)
        config = self._picam.create_video_configuration(
            main={"size": res.as_tuple(), "format": "RGB888"},
            controls={"FrameRate": float(self._framerate)},
        )
        self._picam.configure(config)

        # Apply pending controls (flip, etc.)
        if self._pending_controls:
            self._picam.set_controls(self._pending_controls)

        self._picam.start()
        self._running = True
        log.info("picamera2 started")

    def stop(self) -> None:
        self._running = False
        if self._picam is not None:
            self._picam.stop()
            self._picam.close()
            self._picam = None
        log.info("picamera2 stopped")

    def capture_jpeg(self) -> bytes:
        if self._picam is None:
            raise RuntimeError("Camera not started")
        buf = io.BytesIO()
        with self._lock:
            self._picam.capture_file(buf, format="jpeg")
        buf.seek(0)
        return buf.read()

    # ---------------------------------------------------------------------------
    # Image controls
    # ---------------------------------------------------------------------------

    def _set_control(self, **kwargs) -> None:
        if self._picam is not None:
            try:
                self._picam.set_controls(kwargs)
            except Exception as e:
                log.warning("Failed to set control %s: %s", kwargs, e)
        else:
            self._pending_controls.update(kwargs)

    def set_brightness(self, value: float) -> None:
        # picamera2: Brightness -1.0 … 1.0
        self._set_control(Brightness=max(-1.0, min(1.0, value)))

    def set_contrast(self, value: float) -> None:
        # picamera2: Contrast 0.0 … 32.0
        self._set_control(Contrast=max(0.0, min(32.0, value)))

    def set_saturation(self, value: float) -> None:
        self._set_control(Saturation=max(0.0, min(32.0, value)))

    def set_sharpness(self, value: float) -> None:
        self._set_control(Sharpness=max(0.0, min(16.0, value)))

    def set_exposure_mode(self, mode: str) -> None:
        from libcamera import controls as lc

        mode_map = {
            "auto": lc.AeExposureModeEnum.Normal,
            "sport": lc.AeExposureModeEnum.Short,
            "night": lc.AeExposureModeEnum.Long,
        }
        lc_mode = mode_map.get(mode)
        if lc_mode is not None:
            self._set_control(AeExposureMode=lc_mode)

    def set_awb_mode(self, mode: str) -> None:
        from libcamera import controls as lc

        mode_map = {
            "auto": lc.AwbModeEnum.Auto,
            "sunlight": lc.AwbModeEnum.Daylight,
            "cloudy": lc.AwbModeEnum.Cloudy,
            "shade": lc.AwbModeEnum.Shade,
            "tungsten": lc.AwbModeEnum.Tungsten,
            "fluorescent": lc.AwbModeEnum.Fluorescent,
            "incandescent": lc.AwbModeEnum.Incandescent,
        }
        lc_mode = mode_map.get(mode)
        if lc_mode is not None:
            self._set_control(AwbMode=lc_mode)

    def set_vflip(self, enabled: bool) -> None:
        self._set_control(vflip=enabled)

    def set_hflip(self, enabled: bool) -> None:
        self._set_control(hflip=enabled)

    def set_autofocus(self, enabled: bool) -> None:
        if not self.model.has_autofocus:
            return
        from libcamera import controls as lc

        mode = lc.AfModeEnum.Continuous if enabled else lc.AfModeEnum.Manual
        self._set_control(AfMode=mode)

    def set_hdr(self, enabled: bool) -> None:
        if not self.model.has_hdr:
            return
        self._set_control(HdrMode=4 if enabled else 0)  # 4 = HDR night

    def get_v4l2_device(self):
        # CSI cameras appear as /dev/videoN; index 0 → /dev/video0 typically
        return f"/dev/video{self._camera_index}"
