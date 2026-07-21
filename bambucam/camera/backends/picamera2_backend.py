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


def _resolve_control_enum(controls_module, enum_name: str):
    """Resolve a libcamera enum across stable and draft API layouts."""
    enum = getattr(controls_module, enum_name, None)
    if enum is not None:
        return enum
    return getattr(getattr(controls_module, "draft", None), enum_name, None)


class Picamera2Backend(CameraBackend):
    """Camera backend using the picamera2 / libcamera stack."""

    EXPOSURE_MODES = {
        "auto": None,
        "sport": "short",
        "night": "long",
        "manual": "manual",
    }

    # libcamera AwbModeEnum: Auto, Tungsten, Fluorescent, Indoor, Daylight, Cloudy, Custom
    AWB_MODES = {
        "auto": "Auto",
        "sunlight": "Daylight",
        "cloudy": "Cloudy",
        "shade": "Cloudy",  # no Shade in libcamera — nearest equivalent
        "tungsten": "Tungsten",
        "fluorescent": "Fluorescent",
        "incandescent": "Tungsten",  # no Incandescent
        "indoor": "Indoor",
    }

    def __init__(
        self, model: CameraModel, device: str, camera_index: int = 0, enable_lores: bool = True
    ):
        super().__init__(model, device)
        self._camera_index = camera_index
        self._enable_lores = enable_lores  # False → skip lores stream (no RTSP H264 possible)
        self._picam = None
        self._lock = threading.Lock()
        self._resolution: Optional[Resolution] = None
        self._framerate: int = 30
        self._vflip: bool = False
        self._hflip: bool = False
        self._pending_controls: dict = {}
        self._initial_settings: dict = {}  # non-geometry settings, applied after start()
        self._jpeg_quality: int = 85
        self._h264_encoder = None  # active H264Encoder when RTSP recording is running
        self._rtsp_url: Optional[str] = None  # stored so restart() can re-start recording
        self._rtsp_bitrate: int = 2000

    def configure(self, resolution: Resolution, framerate: int, **kwargs) -> None:
        self._resolution = resolution
        self._framerate = framerate
        # Flips are geometry transforms — stored separately, applied via Transform at start()
        if "vflip" in kwargs:
            self._vflip = bool(kwargs["vflip"])
        if "hflip" in kwargs:
            self._hflip = bool(kwargs["hflip"])
        # All other image settings applied after start() via set_* methods
        self._initial_settings = {k: v for k, v in kwargs.items() if k not in ("vflip", "hflip")}

    def start(self) -> None:
        try:
            from picamera2 import Picamera2
        except ImportError:
            raise RuntimeError(
                "picamera2 is not installed. Run: sudo apt install python3-picamera2"
            )
        try:
            from libcamera import Transform
        except ImportError:
            from picamera2 import Transform

        res = self._resolution or self.model.max_resolution
        log.info(
            "Starting picamera2 backend: %s @ %s %dfps (vflip=%s hflip=%s)",
            self.model.name,
            res,
            self._framerate,
            self._vflip,
            self._hflip,
        )

        self._picam = Picamera2(self._camera_index)

        # lores stream (YUV420) — only when RTSP/H264 is needed.
        # Use half the main resolution (preserves aspect ratio, reduces ISP/GPU
        # load significantly on slower Pi models), capped at 640×360.
        # Must be strictly smaller than main; YUV420 requires even dimensions.
        lores_stream = None
        if self._enable_lores:
            lores_w = min((res.width // 2) & ~1, 640)
            lores_h = min((res.height // 2) & ~1, 360)
            if lores_w >= res.width:
                lores_w = max(2, res.width - 2) & ~1
            if lores_h >= res.height:
                lores_h = max(2, res.height - 2) & ~1
            lores_stream = {"size": (lores_w, lores_h), "format": "YUV420"}

        config = self._picam.create_video_configuration(
            main={"size": res.as_tuple(), "format": "RGB888"},
            lores=lores_stream,
            controls={"FrameRate": float(self._framerate)},
            transform=Transform(hflip=self._hflip, vflip=self._vflip),
        )
        self._picam.configure(config)

        if self._pending_controls:
            self._set_control(**self._pending_controls)

        self._picam.start()
        self._running = True
        self._picam.options["quality"] = self._jpeg_quality

        # Apply image controls from config (brightness, AWB, exposure, etc.)
        for key, value in self._initial_settings.items():
            setter = getattr(self, f"set_{key}", None)
            if callable(setter):
                try:
                    setter(value)
                except Exception as e:
                    log.warning("Failed to apply initial setting %s=%r: %s", key, value, e)

        # Re-start H264 recording if it was active before a camera restart
        if self._rtsp_url is not None:
            try:
                self.start_rtsp_recording(self._rtsp_url, self._rtsp_bitrate)
            except Exception as e:
                log.warning("Failed to restart H264 recording after camera restart: %s", e)

        log.info("picamera2 started")

    def stop(self) -> None:
        self._running = False
        if self._picam is not None:
            try:
                # clear_url=True prevents a race: start() won't auto-restart H264 while
                # the RTSPStreamer monitor is also trying to restart it concurrently.
                self.stop_rtsp_recording(clear_url=True)
            except Exception as e:
                log.warning("Error stopping RTSP recording during shutdown: %s", e)
            try:
                self._picam.stop()
            except Exception as e:
                log.warning("Error stopping picamera2: %s", e)
            try:
                self._picam.close()
            except Exception as e:
                log.warning("Error closing picamera2: %s", e)
            self._picam = None
        log.info("picamera2 stopped")

    def capture_jpeg(self, quality: Optional[int] = None) -> bytes:
        if self._picam is None:
            raise RuntimeError("Camera not started")
        buf = io.BytesIO()
        with self._lock:
            if quality is not None:
                # Temporarily set quality for this capture only
                prev = self._picam.options.get("quality", self._jpeg_quality)
                self._picam.options["quality"] = quality
                self._picam.capture_file(buf, format="jpeg")
                self._picam.options["quality"] = prev
            else:
                self._picam.capture_file(buf, format="jpeg")
        buf.seek(0)
        return buf.read()

    def set_jpeg_quality(self, value: int) -> None:
        self._jpeg_quality = max(1, min(100, int(value)))
        if self._picam is not None:
            self._picam.options["quality"] = self._jpeg_quality

    # ---------------------------------------------------------------------------
    # Image controls
    # ---------------------------------------------------------------------------

    def _set_control(self, **kwargs) -> None:
        if self._picam is not None:
            available_controls = getattr(self._picam, "camera_controls", None)
            if available_controls is not None:
                unsupported = [name for name in kwargs if name not in available_controls]
                if unsupported:
                    log.warning("Camera does not support control(s): %s", ", ".join(unsupported))
                    return
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

        _enum = _resolve_control_enum(lc, "AeExposureModeEnum")
        if _enum is None:
            log.warning("Exposure modes are not supported by this libcamera version")
            return
        mode_map = {
            "auto": getattr(_enum, "Normal", None),
            "sport": getattr(_enum, "Short", None),
            "night": getattr(_enum, "Long", None),
        }
        lc_mode = mode_map.get(mode)
        if lc_mode is not None:
            self._set_control(AeExposureMode=lc_mode)
        else:
            log.warning("Exposure mode %r not supported by this libcamera version", mode)

    def set_awb_mode(self, mode: str) -> None:
        from libcamera import controls as lc

        # libcamera AwbModeEnum: Auto, Tungsten, Fluorescent, Indoor, Daylight, Cloudy, Custom
        # "shade" and "incandescent" are not in the enum; map to nearest equivalent.
        _enum = _resolve_control_enum(lc, "AwbModeEnum")
        if _enum is None:
            log.warning("AWB modes are not supported by this libcamera version")
            return
        mode_map = {
            "auto": getattr(_enum, "Auto", None),
            "sunlight": getattr(_enum, "Daylight", None),
            "cloudy": getattr(_enum, "Cloudy", None),
            "shade": getattr(_enum, "Cloudy", None),  # no Shade in libcamera
            "tungsten": getattr(_enum, "Tungsten", None),
            "fluorescent": getattr(_enum, "Fluorescent", None),
            "incandescent": getattr(_enum, "Tungsten", None),  # no Incandescent
            "indoor": getattr(_enum, "Indoor", None),
        }
        lc_mode = mode_map.get(mode)
        if lc_mode is not None:
            self._set_control(AwbMode=lc_mode)
        else:
            log.warning("AWB mode %r not supported by this libcamera version", mode)

    def set_vflip(self, enabled: bool) -> None:
        # Flips require Transform at configure time — update state for next restart.
        self._vflip = enabled

    def set_hflip(self, enabled: bool) -> None:
        self._hflip = enabled

    def set_autofocus(self, enabled: bool) -> None:
        if not self.model.has_autofocus:
            return
        from libcamera import controls as lc

        _enum = _resolve_control_enum(lc, "AfModeEnum")
        if _enum is None:
            log.warning("Autofocus is not supported by this libcamera version")
            return
        mode = getattr(_enum, "Continuous", None) if enabled else getattr(_enum, "Manual", None)
        if mode is not None:
            self._set_control(AfMode=mode)

    def set_hdr(self, enabled: bool) -> None:
        if not self.model.has_hdr:
            return
        from libcamera import controls as lc

        _enum = _resolve_control_enum(lc, "HdrModeEnum")
        if _enum is None:
            log.warning("HDR is not supported by this libcamera version")
            return
        if enabled:
            # MultiExposure is the standard HDR mode for IMX708; fall back to SingleExposure
            mode = getattr(_enum, "MultiExposure", None) or getattr(_enum, "SingleExposure", None)
        else:
            mode = getattr(_enum, "Off", None)
        if mode is not None:
            self._set_control(HdrMode=mode)

    def set_noise_reduction(self, mode: str) -> None:
        from libcamera import controls as lc

        _enum = _resolve_control_enum(lc, "NoiseReductionModeEnum")
        if _enum is None:
            log.warning("Noise reduction is not supported by this libcamera version")
            return

        mode_map = {
            "off": getattr(_enum, "Off", None),
            "minimal": getattr(_enum, "Minimal", None),
            "fast": getattr(_enum, "Fast", None),
            "high_quality": getattr(_enum, "HighQuality", None),
        }
        lc_mode = mode_map.get(mode)
        if lc_mode is not None:
            self._set_control(NoiseReductionMode=lc_mode)
        else:
            log.warning("Noise reduction mode %r not recognised", mode)

    # ---------------------------------------------------------------------------
    # RTSP via picamera2 H264Encoder (avoids V4L2 device conflict)
    # ---------------------------------------------------------------------------

    def start_rtsp_recording(self, rtsp_url: str, bitrate_kbps: int = 2000) -> None:
        """
        Encode H264 in-process and publish to MediaMTX via RTSP.
        This avoids the V4L2 device conflict that arises when ffmpeg tries to
        open /dev/videoN while picamera2 already holds it.
        """
        try:
            from picamera2.encoders import H264Encoder
            from picamera2.outputs import FfmpegOutput
        except ImportError:
            raise RuntimeError("picamera2 H264Encoder not available")

        if not self._running or self._picam is None:
            raise RuntimeError("Camera must be started before RTSP recording")

        if self._h264_encoder is not None:
            log.warning("H264 recording already active — stopping before restart")
            self.stop_rtsp_recording()

        self._rtsp_url = rtsp_url
        self._rtsp_bitrate = bitrate_kbps

        self._h264_encoder = H264Encoder(
            bitrate=bitrate_kbps * 1000,
            iperiod=self._framerate * 2,  # keyframe every 2 s
        )
        output = FfmpegOutput(f"-f rtsp {rtsp_url}")
        # name="lores" encodes the YUV420 lores stream, leaving the RGB888
        # main stream free for concurrent MJPEG capture_file() calls.
        try:
            self._picam.start_recording(self._h264_encoder, output, name="lores")
        except Exception as e:
            self._h264_encoder = None
            raise RuntimeError(f"H264 recording failed to start: {e}") from e
        log.info("H264 RTSP recording started → %s at %d kbps", rtsp_url, bitrate_kbps)

    def stop_rtsp_recording(self, clear_url: bool = False) -> None:
        if self._picam is not None and self._h264_encoder is not None:
            try:
                self._picam.stop_recording()
            except Exception as e:
                log.warning("Error stopping H264 recording: %s", e)
        self._h264_encoder = None
        if clear_url:
            self._rtsp_url = None

    @property
    def is_rtsp_recording(self) -> bool:
        return (
            self._picam is not None
            and self._h264_encoder is not None
            and getattr(self._picam, "recording", False)
        )

    def get_v4l2_device(self):
        # CSI cameras appear as /dev/videoN; index 0 → /dev/video0 typically
        return f"/dev/video{self._camera_index}"
