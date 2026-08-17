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

# The VC4 H.264 hardware encoder is capped at 1920 in each dimension
# (MAX_W_CODEC/MAX_H_CODEC in bcm2835-v4l2-codec.c). The driver silently clamps
# anything larger instead of failing, which surfaces as corrupt output or a
# stalled encoder — so the size is validated here rather than left to the driver.
H264_MAX_DIMENSION = 1920

# Fallback size for the encode stream when the caller does not choose one.
DEFAULT_ENCODE_SIZE = (640, 360)


def _resolve_control_enum(controls_module, enum_name: str):
    """Resolve a libcamera enum across stable and draft API layouts."""
    enum = getattr(controls_module, enum_name, None)
    if enum is not None:
        return enum
    return getattr(getattr(controls_module, "draft", None), enum_name, None)


def _rectangle_tuple(value) -> Optional[tuple[int, int, int, int]]:
    """Normalise libcamera Rectangle objects and tuple-like values."""
    if value is None:
        return None
    attributes = ("x", "y", "width", "height")
    if all(hasattr(value, attribute) for attribute in attributes):
        return tuple(int(getattr(value, attribute)) for attribute in attributes)
    try:
        values = tuple(int(item) for item in value)
    except (TypeError, ValueError):
        return None
    if len(values) != 4:
        return None
    return values[0], values[1], values[2], values[3]


class MJPEGFrameBuffer(io.BufferedIOBase):
    """
    Single-slot, thread-safe sink for the frames of picamera2's MJPEG encoder.

    picamera2's FileOutput insists on an io.BufferedIOBase ("Must pass
    io.BufferedIOBase") and calls write() once per finished JPEG frame, followed
    by flush(). Only the newest frame is kept: a consumer that reads slower than
    the encoder produces must miss frames, never grow a queue — an unbounded
    backlog here is exactly the kind of leak this buffer has to avoid.

    Frames are numbered with a monotonically increasing sequence so that a
    reader can block until a frame it has not seen yet arrives.
    """

    def __init__(self) -> None:
        super().__init__()
        self._condition = threading.Condition()
        self._frame: Optional[bytes] = None
        self._sequence: int = 0

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:
        # One call == one complete JPEG frame (FileOutput writes whole frames).
        frame = bytes(b)
        with self._condition:
            self._frame = frame
            self._sequence += 1
            self._condition.notify_all()
        return len(frame)

    def flush(self) -> None:
        # Nothing is held back in write(), so there is nothing to flush.
        pass

    def clear(self) -> None:
        """Drop the buffered frame so no consumer can read a stale image."""
        with self._condition:
            self._frame = None
            self._sequence += 1
            self._condition.notify_all()

    @property
    def sequence(self) -> int:
        with self._condition:
            return self._sequence

    def latest(self) -> tuple[Optional[bytes], int]:
        """Return the buffered frame and its sequence number without blocking."""
        with self._condition:
            return self._frame, self._sequence

    def wait_for_frame(
        self, timeout: Optional[float] = 1.0, after_sequence: Optional[int] = None
    ) -> tuple[Optional[bytes], int]:
        """
        Return (frame, sequence) for the newest frame past ``after_sequence``.

        Returns (None, sequence) when the timeout expires without a new frame.
        """

        def ready() -> bool:
            return self._frame is not None and (
                after_sequence is None or self._sequence > after_sequence
            )

        with self._condition:
            if self._condition.wait_for(ready, timeout):
                return self._frame, self._sequence
            return None, self._sequence


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
        self,
        model: CameraModel,
        device: str,
        camera_index: int = 0,
        enable_lores: bool = True,
        encode_size: Optional[tuple[int, int]] = None,
    ):
        super().__init__(model, device)
        self._camera_index = camera_index
        self._enable_lores = enable_lores  # False → skip lores stream (no RTSP H264 possible)
        self._requested_encode_size = encode_size
        self._encode_size: Optional[tuple[int, int]] = None
        self._picam = None
        self._lock = threading.Lock()
        self._resolution: Optional[Resolution] = None
        self._framerate: int = 30
        self._vflip: bool = False
        self._hflip: bool = False
        self._zoom: float = 1.0
        self._pending_controls: dict = {}
        self._initial_settings: dict = {}  # non-geometry settings, applied after start()
        self._jpeg_quality: int = 85
        self._h264_encoder = None  # active H264Encoder when RTSP recording is running
        self._rtsp_url: Optional[str] = None  # stored so restart() can re-start recording
        self._rtsp_bitrate: int = 2000
        self._h264_output = None  # kept so liveness can see a broken ffmpeg output
        self._mjpeg_encoder = None  # active MJPEGEncoder when the MJPEG stream is running
        self._mjpeg_buffer: Optional[MJPEGFrameBuffer] = None
        self._mjpeg_stream_name: Optional[str] = None
        self._mjpeg_active: bool = False  # stored so start() can re-attach after a restart
        self._mjpeg_bitrate: int = 4000
        # Per-caller "newest frame already handed out", so every consumer thread
        # gets each frame once instead of re-reading the same one.
        self._mjpeg_seen = threading.local()

    def _resolve_encode_size(self, main: Resolution) -> tuple[int, int]:
        """
        Pick the size of the H264 encode stream.

        The requested size is capped by what the hardware encoder accepts, by the
        main stream (libcamera rejects a larger lores), and rounded down to even
        dimensions for YUV420. Without a request, fall back to a small preview-sized
        stream, which is what slower Pi models can sustain.
        """
        requested = self._requested_encode_size or DEFAULT_ENCODE_SIZE
        width = min(int(requested[0]), main.width, H264_MAX_DIMENSION) & ~1
        height = min(int(requested[1]), main.height, H264_MAX_DIMENSION) & ~1
        if (width, height) != tuple(int(value) for value in requested):
            log.info(
                "H264 encode stream reduced from %dx%d to %dx%d "
                "(camera mode %s, encoder limit %d)",
                int(requested[0]),
                int(requested[1]),
                width,
                height,
                main,
                H264_MAX_DIMENSION,
            )
        return max(32, width), max(32, height)

    @property
    def encode_resolution(self) -> Optional[tuple[int, int]]:
        """Size of the stream the H264 encoder publishes, once the camera started."""
        return self._encode_size

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

        # lores stream (YUV420) — the H264 encode source, only set up when
        # RTSP/H264 is needed. YUV420 requires even dimensions and libcamera
        # requires lores to be no larger than main.
        lores_stream = None
        if self._enable_lores:
            self._encode_size = self._resolve_encode_size(res)
            lores_stream = {"size": self._encode_size, "format": "YUV420"}
            log.info("H264 encode stream: %dx%d", *self._encode_size)

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

        # Re-attach the MJPEG encoder if it was active before a camera restart
        if self._mjpeg_active:
            # Any encoder left over belongs to the previous Picamera2 instance and
            # is dead with it — drop it so the re-attach is not treated as a no-op.
            self._mjpeg_encoder = None
            self._mjpeg_buffer = None
            try:
                self.start_mjpeg_stream(self._mjpeg_bitrate)
            except Exception as e:
                log.warning("Failed to restart MJPEG streaming after camera restart: %s", e)

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
            # Detach the MJPEG encoder, but remember that it was running: unlike
            # RTSP there is no external monitor re-attaching it, so start() has to
            # bring the stream back after a restart() or watchdog restart.
            was_streaming = self._mjpeg_active
            try:
                self.stop_mjpeg_stream()
            except Exception as e:
                log.warning("Error stopping MJPEG stream during shutdown: %s", e)
            self._mjpeg_active = was_streaming
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

    def _scaler_crop_limits(
        self,
    ) -> Optional[tuple[tuple[int, int, int, int], tuple[int, int, int, int]]]:
        if self._picam is None:
            return None
        controls = getattr(self._picam, "camera_controls", {}) or {}
        info = controls.get("ScalerCrop")
        if info is None:
            return None
        if all(hasattr(info, attribute) for attribute in ("min", "max", "default")):
            minimum, maximum, default = info.min, info.max, info.default
        else:
            try:
                minimum, maximum, default = info[:3]
            except (TypeError, ValueError):
                return None
        minimum_rect = _rectangle_tuple(minimum)
        maximum_rect = _rectangle_tuple(maximum)
        default_rect = _rectangle_tuple(default)
        bounds = default_rect or maximum_rect
        if bounds is None or minimum_rect is None or bounds[2] <= 0 or bounds[3] <= 0:
            return None
        return bounds, minimum_rect

    @property
    def supports_zoom(self) -> bool:
        if self._picam is None:
            return True
        controls = getattr(self._picam, "camera_controls", {}) or {}
        return "ScalerCrop" in controls

    @property
    def max_zoom(self) -> float:
        limits = self._scaler_crop_limits()
        if limits is None:
            return 8.0 if self._picam is None else 1.0
        bounds, minimum = limits
        if minimum[2] <= 0 or minimum[3] <= 0:
            return 8.0
        return max(1.0, min(8.0, bounds[2] / minimum[2], bounds[3] / minimum[3]))

    def set_zoom(self, value: float) -> None:
        requested = max(1.0, min(8.0, float(value)))
        self._zoom = requested
        if self._picam is None:
            return
        limits = self._scaler_crop_limits()
        if limits is None:
            log.warning("Digital zoom is not supported by this camera/libcamera version")
            return
        bounds, _minimum = limits
        zoom = min(requested, self.max_zoom)
        width = max(2, int(bounds[2] / zoom))
        height = max(2, int(bounds[3] / zoom))
        width -= width % 2
        height -= height % 2
        x = bounds[0] + (bounds[2] - width) // 2
        y = bounds[1] + (bounds[3] - height) // 2
        self._set_control(ScalerCrop=(x, y, width, height))

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
        self._h264_output = FfmpegOutput(f"-f rtsp {rtsp_url}")
        # name="lores" encodes the YUV420 lores stream, leaving the RGB888
        # main stream free for concurrent MJPEG capture_file() calls.
        try:
            # start_encoder(), not start_recording(): the camera is already running
            # here, and start_recording() would additionally (re-)start it.
            self._picam.start_encoder(self._h264_encoder, self._h264_output, name="lores")
        except Exception as e:
            self._h264_encoder = None
            raise RuntimeError(f"H264 recording failed to start: {e}") from e
        log.info("H264 RTSP recording started → %s at %d kbps", rtsp_url, bitrate_kbps)

    def stop_rtsp_recording(self, clear_url: bool = False) -> None:
        if self._picam is not None and self._h264_encoder is not None:
            try:
                # stop_encoder(), not stop_recording(): the latter is stop() plus
                # stop_encoder(None), so it would stop the camera itself and every
                # other encoder along with it. Ending an RTSP session must leave
                # the camera running for MJPEG, snapshots, and timelapse.
                self._picam.stop_encoder(self._h264_encoder)
            except Exception as e:
                # Encoder.stop() raises when it was already stopped, which is
                # expected if the encoder died on its own before we got here.
                log.debug("H264 encoder was already detached: %s", e)
        self._h264_encoder = None
        self._h264_output = None
        if clear_url:
            self._rtsp_url = None

    def _encoder_active(self, encoder, output=None) -> bool:
        """
        Report whether an encoder is attached to a running camera and producing.

        Picamera2 has no `recording` attribute — the flag of that name lives on
        Output, not on the camera — so asking the camera object was always False.
        The camera's own `started` flag plus the encoder's membership in
        `picam.encoders` and its `running` flag are the real signals. Membership
        and `running` are checked together because stop_encoder() clears `running`
        before it removes the encoder from the set.
        """
        if self._picam is None or encoder is None:
            return False
        if not getattr(self._picam, "started", False):
            return False
        encoders = getattr(self._picam, "encoders", None)
        try:
            attached = encoder in encoders
        except TypeError:  # not a container — treat as detached rather than guess
            return False
        if not (attached and getattr(encoder, "running", False)):
            return False
        # An FfmpegOutput whose subprocess died keeps dropping frames while the
        # encoder still reports itself as running, so the publisher looks healthy
        # while nothing reaches the server.
        return not getattr(output, "output_broken", False)

    @property
    def is_rtsp_recording(self) -> bool:
        return self._encoder_active(self._h264_encoder, self._h264_output)

    # ---------------------------------------------------------------------------
    # MJPEG via picamera2 MJPEGEncoder (replaces per-frame capture_file())
    # ---------------------------------------------------------------------------

    def start_mjpeg_stream(self, bitrate_kbps: int = 4000) -> None:
        """
        Encode MJPEG in-process and push finished frames into a one-slot buffer.

        This replaces the per-frame capture_file() round-trip plus software JPEG
        encode for live streaming; capture_jpeg() stays in place for snapshots and
        timelapse, which need full-resolution stills from the main stream.
        """
        try:
            from picamera2.encoders import MJPEGEncoder
            from picamera2.outputs import FileOutput
        except ImportError:
            raise RuntimeError("picamera2 MJPEGEncoder not available")

        if not self._running or self._picam is None:
            raise RuntimeError("Camera must be started before MJPEG streaming")

        if self._mjpeg_encoder is not None:
            log.debug("MJPEG stream already active — ignoring start request")
            return

        self._mjpeg_bitrate = bitrate_kbps
        # Encode the lores stream when there is one — it is smaller and leaves the
        # RGB888 main stream free for snapshots — otherwise encode main directly.
        stream_name = "lores" if self._encode_size is not None else "main"

        # MJPEGEncoder is the V4L2 hardware encoder on Pi 0-4/CM4 and is silently
        # aliased to the software LibavMjpegEncoder on Pi 5. Their keyword
        # signatures differ; only the first positional argument (bitrate) is
        # common to both, so it must be passed positionally and without keywords.
        self._mjpeg_encoder = MJPEGEncoder(bitrate_kbps * 1000)
        self._mjpeg_buffer = MJPEGFrameBuffer()
        output = FileOutput(self._mjpeg_buffer)
        try:
            # start_encoder(), not start_recording(): the camera is already running
            # here, and start_recording() would additionally (re-)start it.
            self._picam.start_encoder(self._mjpeg_encoder, output, name=stream_name)
        except Exception as e:
            self._mjpeg_encoder = None
            self._mjpeg_buffer = None
            raise RuntimeError(f"MJPEG streaming failed to start: {e}") from e
        self._mjpeg_stream_name = stream_name
        self._mjpeg_active = True
        log.info("MJPEG stream started on the %s stream at %d kbps", stream_name, bitrate_kbps)

    def stop_mjpeg_stream(self) -> None:
        if self._picam is not None and self._mjpeg_encoder is not None:
            try:
                # stop_encoder(), not stop_recording(): the latter is stop() plus
                # stop_encoder(None), so it would stop the camera itself and every
                # other encoder — including the H264/RTSP one — along with it.
                self._picam.stop_encoder(self._mjpeg_encoder)
            except Exception as e:
                # Encoder.stop() raises when it was already stopped, which is
                # expected if the encoder died on its own before we got here.
                log.debug("MJPEG encoder was already detached: %s", e)
        self._mjpeg_encoder = None
        self._mjpeg_stream_name = None
        self._mjpeg_active = False
        if self._mjpeg_buffer is not None:
            # Drop the last frame so a later consumer cannot read a stale image.
            self._mjpeg_buffer.clear()
            self._mjpeg_buffer = None

    def latest_jpeg(self, timeout: float = 1.0) -> Optional[bytes]:
        """
        Return the newest encoded JPEG frame this caller has not seen yet.

        Blocks until such a frame arrives or the timeout expires; returns None on
        timeout and when the MJPEG stream is not running. Safe to call from any
        number of threads — each caller thread is served every frame once.
        """
        buffer = self._mjpeg_buffer
        if buffer is None:
            return None
        seen = getattr(self._mjpeg_seen, "state", None)
        # The buffer is recreated per stream, so its sequence restarts at 0;
        # tracking the buffer alongside the number keeps stale counts harmless.
        after_sequence = seen[1] if seen is not None and seen[0] is buffer else None
        frame, sequence = buffer.wait_for_frame(timeout, after_sequence)
        if frame is None:
            return None
        self._mjpeg_seen.state = (buffer, sequence)
        return frame

    @property
    def is_mjpeg_streaming(self) -> bool:
        return self._encoder_active(self._mjpeg_encoder)

    @property
    def mjpeg_resolution(self) -> Optional[tuple[int, int]]:
        """Size of the stream the MJPEG encoder reads, while streaming."""
        if self._mjpeg_encoder is None:
            return None
        if self._mjpeg_stream_name == "lores" and self._encode_size is not None:
            return self._encode_size
        res = self._resolution or self.model.max_resolution
        return (int(res.width), int(res.height))

    def get_v4l2_device(self):
        # CSI cameras appear as /dev/videoN; index 0 → /dev/video0 typically
        return f"/dev/video{self._camera_index}"
