"""Tests for compatibility across picamera2/libcamera releases."""

import io
import logging
import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from bambucam.camera.backends.picamera2_backend import MJPEGFrameBuffer, Picamera2Backend
from bambucam.camera.models import CAMERA_V3, Resolution


def _backend() -> Picamera2Backend:
    return Picamera2Backend(CAMERA_V3, "libcamera:0")


def _libcamera_with(controls) -> ModuleType:
    module = ModuleType("libcamera")
    module.controls = controls
    return module


def _control_enums():
    return SimpleNamespace(
        AeExposureModeEnum=SimpleNamespace(
            Normal="normal-value", Short="short-value", Long="long-value"
        ),
        AwbModeEnum=SimpleNamespace(
            Auto="awb-auto-value",
            Daylight="daylight-value",
            Cloudy="cloudy-value",
            Tungsten="tungsten-value",
            Fluorescent="fluorescent-value",
            Indoor="indoor-value",
        ),
        AfModeEnum=SimpleNamespace(Continuous="continuous-value", Manual="manual-value"),
        HdrModeEnum=SimpleNamespace(
            Off="hdr-off-value",
            MultiExposure="multi-exposure-value",
            SingleExposure="single-exposure-value",
        ),
        NoiseReductionModeEnum=SimpleNamespace(
            Off="noise-off-value",
            Minimal="minimal-value",
            Fast="fast-value",
            HighQuality="high-quality-value",
        ),
    )


def _apply_enum_controls(backend: Picamera2Backend) -> None:
    backend.set_exposure_mode("sport")
    backend.set_awb_mode("sunlight")
    backend.set_autofocus(True)
    backend.set_hdr(True)
    backend.set_noise_reduction("high_quality")


EXPECTED_CONTROLS = {
    "AeExposureMode": "short-value",
    "AwbMode": "daylight-value",
    "AfMode": "continuous-value",
    "HdrMode": "multi-exposure-value",
    "NoiseReductionMode": "high-quality-value",
}


def test_enum_controls_use_top_level_enums() -> None:
    controls = _control_enums()

    with patch.dict(sys.modules, {"libcamera": _libcamera_with(controls)}):
        backend = _backend()
        _apply_enum_controls(backend)

    assert backend._pending_controls == EXPECTED_CONTROLS


def test_enum_controls_use_draft_enums() -> None:
    controls = SimpleNamespace(draft=_control_enums())

    with patch.dict(sys.modules, {"libcamera": _libcamera_with(controls)}):
        backend = _backend()
        _apply_enum_controls(backend)

    assert backend._pending_controls == EXPECTED_CONTROLS


def test_missing_control_enums_are_ignored(caplog) -> None:
    controls = SimpleNamespace()

    with patch.dict(sys.modules, {"libcamera": _libcamera_with(controls)}):
        backend = _backend()
        with caplog.at_level(logging.WARNING):
            _apply_enum_controls(backend)

    assert backend._pending_controls == {}
    assert "Exposure modes are not supported" in caplog.text
    assert "AWB modes are not supported" in caplog.text
    assert "Autofocus is not supported" in caplog.text
    assert "HDR is not supported" in caplog.text
    assert "not supported by this libcamera version" in caplog.text


def test_camera_control_capabilities_are_checked(caplog) -> None:
    backend = _backend()
    picam = SimpleNamespace(camera_controls={"Brightness": object()}, set_controls=Mock())
    backend._picam = picam

    with caplog.at_level(logging.WARNING):
        backend._set_control(HdrMode="multi-exposure-value")

    picam.set_controls.assert_not_called()
    assert "Camera does not support control(s): HdrMode" in caplog.text


def test_digital_zoom_uses_centered_scaler_crop() -> None:
    backend = _backend()
    picam = SimpleNamespace(
        camera_controls={
            "ScalerCrop": (
                (0, 0, 64, 64),
                (0, 0, 4608, 2592),
                (0, 0, 4608, 2592),
            )
        },
        set_controls=Mock(),
    )
    backend._picam = picam

    backend.set_zoom(2.0)

    assert backend.supports_zoom is True
    assert backend.max_zoom == 8.0
    picam.set_controls.assert_called_once_with({"ScalerCrop": (1152, 648, 2304, 1296)})


def test_digital_zoom_is_hidden_when_scaler_crop_is_unavailable(caplog) -> None:
    backend = _backend()
    picam = SimpleNamespace(camera_controls={}, set_controls=Mock())
    backend._picam = picam

    with caplog.at_level(logging.WARNING):
        backend.set_zoom(2.0)

    assert backend.supports_zoom is False
    assert backend.max_zoom == 1.0
    picam.set_controls.assert_not_called()
    assert "Digital zoom is not supported" in caplog.text


class _FakeEncoder:
    """
    Stand-in for a picamera2 Encoder.

    The real class carries a mutable `running` flag (encoders/encoder.py) that the
    backend's liveness check reads, so a plain string or Mock cannot model it.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.running = False

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.name} encoder>"


class _FakeOutput:
    """Stand-in for a picamera2 Output (FileOutput and friends)."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.name} output>"


class _FakeFfmpegOutput(_FakeOutput):
    """FfmpegOutput additionally flags a dead ffmpeg subprocess."""

    def __init__(self, name: str = "ffmpeg") -> None:
        super().__init__(name)
        self.output_broken = False


def _fake_camera() -> Mock:
    """
    Mock Picamera2 that models the real attach/detach contract.

    Picamera2 has no `recording` attribute; liveness is `started` plus the
    encoder's membership in `encoders` plus its own `running` flag.
    """
    picam = Mock()
    picam.started = True
    picam.encoders = set()

    def start_encoder(encoder, output=None, name=None, **kwargs) -> None:
        encoder.running = True
        picam.encoders.add(encoder)

    def stop_encoder(encoder) -> None:
        # Encoder.stop() raises on an encoder that is not running.
        if not getattr(encoder, "running", False):
            raise RuntimeError("Encoder already stopped")
        encoder.running = False
        picam.encoders.discard(encoder)

    def stop_camera() -> None:
        picam.started = False

    picam.start_encoder = Mock(side_effect=start_encoder)
    picam.stop_encoder = Mock(side_effect=stop_encoder)
    picam.stop = Mock(side_effect=stop_camera)
    return picam


def _running_backend() -> tuple:
    """Return a started backend plus its mocked Picamera2 handle."""
    backend = _backend()
    picam = _fake_camera()
    backend._picam = picam
    backend._running = True
    backend._framerate = 15
    return backend, picam


def _encoder_modules() -> dict:
    """Minimal picamera2.encoders / picamera2.outputs stand-ins."""
    encoders = ModuleType("picamera2.encoders")
    encoders.H264Encoder = Mock(return_value=_FakeEncoder("h264"))
    encoders.MJPEGEncoder = Mock(return_value=_FakeEncoder("mjpeg"))
    outputs = ModuleType("picamera2.outputs")
    outputs.FfmpegOutput = Mock(return_value=_FakeFfmpegOutput())
    outputs.FileOutput = Mock(return_value=_FakeOutput("file"))
    package = ModuleType("picamera2")
    package.encoders = encoders
    package.outputs = outputs
    return {"picamera2": package, "picamera2.encoders": encoders, "picamera2.outputs": outputs}


def _encoder_of(modules: dict, class_name: str) -> _FakeEncoder:
    """The single encoder instance the given stand-in class hands out."""
    return getattr(modules["picamera2.encoders"], class_name).return_value


def _rtsp_backend(bitrate_kbps: int = 2000) -> tuple:
    """Return a backend with an active RTSP recording, its picam and encoder modules."""
    backend, picam = _running_backend()
    modules = _encoder_modules()
    with patch.dict(sys.modules, modules):
        backend.start_rtsp_recording("rtsp://127.0.0.1:8554/cam", bitrate_kbps)
    return backend, picam, modules


def test_rtsp_recording_attaches_encoder_without_restarting_the_camera() -> None:
    backend, picam = _running_backend()
    modules = _encoder_modules()

    with patch.dict(sys.modules, modules):
        backend.start_rtsp_recording("rtsp://127.0.0.1:8554/cam", 2000)

    picam.start_encoder.assert_called_once()
    assert picam.start_encoder.call_args.args[0] is _encoder_of(modules, "H264Encoder")
    assert picam.start_encoder.call_args.kwargs == {"name": "lores"}
    # start_recording() would also (re-)start the camera.
    picam.start_recording.assert_not_called()


def test_stopping_rtsp_recording_leaves_the_camera_running() -> None:
    """Regression: stop_recording() stops the camera and every other encoder."""
    backend, picam, modules = _rtsp_backend()
    encoder = _encoder_of(modules, "H264Encoder")

    backend.stop_rtsp_recording()

    picam.stop_encoder.assert_called_once_with(encoder)
    picam.stop_recording.assert_not_called()
    picam.stop.assert_not_called()
    assert backend._h264_encoder is None
    assert encoder not in picam.encoders


def _started_config(backend: Picamera2Backend, resolution, extra_modules=None) -> dict:
    """Run start() against a mocked Picamera2 and return the stream configuration."""
    picam = _fake_camera()
    picam.create_video_configuration = Mock(return_value={"config": True})
    picam.options = {}  # a real dict: start() assigns the JPEG quality into it
    backend._resolution = resolution
    modules = {
        "picamera2": ModuleType("picamera2"),
        "libcamera": ModuleType("libcamera"),
    }
    # e.g. _encoder_modules(), for a start() that re-attaches encoders
    modules.update(extra_modules or {})
    modules["picamera2"].Picamera2 = Mock(return_value=picam)
    modules["libcamera"].Transform = Mock(return_value="transform")
    with patch.dict(sys.modules, modules):
        backend.start()
    return picam.create_video_configuration.call_args.kwargs


def test_encode_stream_defaults_to_a_preview_size() -> None:
    backend = _backend()
    config = _started_config(backend, Resolution(1920, 1080))

    assert config["lores"]["size"] == (640, 360)
    assert config["lores"]["format"] == "YUV420"
    assert backend.encode_resolution == (640, 360)


def test_requested_encode_size_is_used() -> None:
    backend = Picamera2Backend(CAMERA_V3, "libcamera:0", encode_size=(1280, 720))
    config = _started_config(backend, Resolution(1920, 1080))

    assert config["lores"]["size"] == (1280, 720)


def test_encode_size_is_capped_by_the_hardware_encoder_limit() -> None:
    """The V4L2 codec silently clamps above 1920, so oversized requests are refused here."""
    backend = Picamera2Backend(CAMERA_V3, "libcamera:0", encode_size=(4608, 2592))
    config = _started_config(backend, Resolution(4608, 2592))

    assert config["lores"]["size"] == (1920, 1920)


def test_encode_size_never_exceeds_the_main_stream() -> None:
    backend = Picamera2Backend(CAMERA_V3, "libcamera:0", encode_size=(1920, 1080))
    config = _started_config(backend, Resolution(1280, 720))

    assert config["lores"]["size"] == (1280, 720)


def test_encode_size_is_rounded_to_even_dimensions() -> None:
    backend = Picamera2Backend(CAMERA_V3, "libcamera:0", encode_size=(641, 361))
    config = _started_config(backend, Resolution(1920, 1080))

    assert config["lores"]["size"] == (640, 360)


def test_no_encode_stream_without_lores() -> None:
    backend = Picamera2Backend(CAMERA_V3, "libcamera:0", enable_lores=False)
    config = _started_config(backend, Resolution(1920, 1080))

    assert config["lores"] is None
    assert backend.encode_resolution is None


# ---------------------------------------------------------------------------
# MJPEG encoder path
# ---------------------------------------------------------------------------


def _mjpeg_backend(bitrate_kbps: int = 4000, lores: bool = True) -> tuple:
    """Return a backend with an active MJPEG stream, its picam and encoder modules."""
    backend, picam = _running_backend()
    backend._resolution = Resolution(1280, 720)
    if lores:
        backend._encode_size = (640, 360)
    modules = _encoder_modules()
    with patch.dict(sys.modules, modules):
        backend.start_mjpeg_stream(bitrate_kbps)
    return backend, picam, modules


def test_mjpeg_stream_attaches_encoder_to_the_lores_stream() -> None:
    backend, picam, modules = _mjpeg_backend()

    picam.start_encoder.assert_called_once()
    assert picam.start_encoder.call_args.args[0] is _encoder_of(modules, "MJPEGEncoder")
    assert picam.start_encoder.call_args.kwargs == {"name": "lores"}
    # start_recording() would also (re-)start the camera.
    picam.start_recording.assert_not_called()
    assert backend.is_mjpeg_streaming is True
    assert backend.mjpeg_resolution == (640, 360)


def test_mjpeg_stream_encodes_main_without_a_lores_stream() -> None:
    backend, picam, _modules = _mjpeg_backend(lores=False)

    assert picam.start_encoder.call_args.kwargs == {"name": "main"}
    assert backend.mjpeg_resolution == (1280, 720)


def test_mjpeg_encoder_is_constructed_with_a_positional_bitrate() -> None:
    """MJPEGEncoder is aliased to LibavMjpegEncoder on Pi 5 — only bitrate is common."""
    _backend_, _picam, modules = _mjpeg_backend(3000)

    encoder_cls = modules["picamera2.encoders"].MJPEGEncoder
    encoder_cls.assert_called_once_with(3000 * 1000)
    assert encoder_cls.call_args.args == (3_000_000,)
    assert encoder_cls.call_args.kwargs == {}


def test_mjpeg_output_writes_into_the_backend_frame_buffer() -> None:
    backend, _picam, modules = _mjpeg_backend()

    file_output = modules["picamera2.outputs"].FileOutput
    file_output.assert_called_once_with(backend._mjpeg_buffer)


def test_starting_mjpeg_stream_twice_is_a_no_op() -> None:
    backend, picam, modules = _mjpeg_backend()
    buffer = backend._mjpeg_buffer

    with patch.dict(sys.modules, modules):
        backend.start_mjpeg_stream(4000)

    picam.start_encoder.assert_called_once()
    modules["picamera2.encoders"].MJPEGEncoder.assert_called_once()
    assert backend._mjpeg_buffer is buffer


def test_mjpeg_stream_requires_a_started_camera() -> None:
    backend = _backend()

    with patch.dict(sys.modules, _encoder_modules()):
        try:
            backend.start_mjpeg_stream()
        except RuntimeError as e:
            assert "Camera must be started" in str(e)
        else:  # pragma: no cover - only reached on a regression
            raise AssertionError("start_mjpeg_stream must raise without a running camera")

    assert backend.is_mjpeg_streaming is False
    assert backend.mjpeg_resolution is None


def test_stopping_mjpeg_stream_leaves_the_camera_running() -> None:
    """Regression: stop_recording() stops the camera and every other encoder."""
    backend, picam, modules = _mjpeg_backend()
    buffer = backend._mjpeg_buffer
    buffer.write(b"stale-frame")

    backend.stop_mjpeg_stream()

    picam.stop_encoder.assert_called_once_with(_encoder_of(modules, "MJPEGEncoder"))
    picam.stop_recording.assert_not_called()
    picam.stop.assert_not_called()
    assert backend._mjpeg_encoder is None
    assert backend.is_mjpeg_streaming is False
    assert backend.mjpeg_resolution is None
    # the stale frame must not survive for the next consumer
    assert buffer.latest()[0] is None
    assert backend.latest_jpeg(timeout=0.05) is None


def test_stopping_mjpeg_stream_twice_is_a_no_op() -> None:
    """The second stop must not re-enter stop_encoder, which now raises."""
    backend, picam, modules = _mjpeg_backend()

    backend.stop_mjpeg_stream()
    backend.stop_mjpeg_stream()

    picam.stop_encoder.assert_called_once_with(_encoder_of(modules, "MJPEGEncoder"))
    assert backend.is_mjpeg_streaming is False


def test_stopping_a_dead_mjpeg_encoder_is_swallowed(caplog) -> None:
    """Encoder.stop() raises 'Encoder already stopped' when it died on its own."""
    backend, picam, modules = _mjpeg_backend()
    encoder = _encoder_of(modules, "MJPEGEncoder")
    encoder.running = False  # died on its own, still referenced by the backend

    with caplog.at_level(logging.DEBUG):
        backend.stop_mjpeg_stream()

    picam.stop_encoder.assert_called_once_with(encoder)
    assert "MJPEG encoder was already detached" in caplog.text
    assert backend._mjpeg_encoder is None
    assert backend.is_mjpeg_streaming is False


def test_stopping_mjpeg_stream_without_a_camera_is_silent() -> None:
    backend = _backend()

    backend.stop_mjpeg_stream()

    assert backend.is_mjpeg_streaming is False


def test_latest_jpeg_returns_a_frame_written_through_the_buffer() -> None:
    backend, _picam, _modules = _mjpeg_backend()

    backend._mjpeg_buffer.write(b"jpeg-frame")

    assert backend.latest_jpeg(timeout=0.05) == b"jpeg-frame"
    # the same caller does not get the same frame twice
    assert backend.latest_jpeg(timeout=0.05) is None
    backend._mjpeg_buffer.write(b"next-frame")
    assert backend.latest_jpeg(timeout=0.05) == b"next-frame"


def test_latest_jpeg_returns_none_on_timeout() -> None:
    backend, _picam, _modules = _mjpeg_backend()

    assert backend.latest_jpeg(timeout=0.05) is None


def test_latest_jpeg_returns_none_when_not_streaming() -> None:
    backend, _picam = _running_backend()

    assert backend.latest_jpeg(timeout=0.05) is None


def test_latest_jpeg_serves_every_consumer_thread() -> None:
    backend, _picam, _modules = _mjpeg_backend()
    backend._mjpeg_buffer.write(b"shared-frame")
    results = []

    def consume() -> None:
        results.append(backend.latest_jpeg(timeout=0.5))

    threads = [threading.Thread(target=consume) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert results == [b"shared-frame"] * 3


def test_latest_jpeg_unblocks_when_a_frame_arrives() -> None:
    backend, _picam, _modules = _mjpeg_backend()
    buffer = backend._mjpeg_buffer
    threading.Timer(0.02, lambda: buffer.write(b"late-frame")).start()

    assert backend.latest_jpeg(timeout=2.0) == b"late-frame"


def test_frame_buffer_keeps_only_the_newest_frame() -> None:
    buffer = MJPEGFrameBuffer()

    for index in range(50):
        buffer.write(f"frame-{index}".encode())

    frame, sequence = buffer.latest()
    assert frame == b"frame-49"
    assert sequence == 50  # no queue, just a counter


def test_frame_buffer_satisfies_file_output() -> None:
    """FileOutput raises RuntimeError('Must pass io.BufferedIOBase') otherwise."""
    buffer = MJPEGFrameBuffer()

    assert isinstance(buffer, io.BufferedIOBase)
    assert buffer.writable() is True
    assert buffer.write(b"frame") == 5
    assert buffer.flush() is None


def test_mjpeg_streaming_is_restored_after_a_camera_restart() -> None:
    backend, _picam, _modules = _mjpeg_backend()

    backend.stop()
    assert backend.is_mjpeg_streaming is False

    _started_config(backend, Resolution(1280, 720), extra_modules=_encoder_modules())

    assert backend.is_mjpeg_streaming is True
    backend._picam.start_encoder.assert_called_once()
    assert backend._picam.start_encoder.call_args.kwargs == {"name": "lores"}


def test_mjpeg_streaming_is_not_restored_when_it_was_stopped() -> None:
    backend, _picam, _modules = _mjpeg_backend()

    backend.stop_mjpeg_stream()
    backend.stop()
    _started_config(backend, Resolution(1280, 720), extra_modules=_encoder_modules())

    assert backend.is_mjpeg_streaming is False
    backend._picam.start_encoder.assert_not_called()


# ---------------------------------------------------------------------------
# Encoder liveness
#
# Picamera2 has no `recording` attribute, so the old
# getattr(picam, "recording", False) made both properties permanently False.
# ---------------------------------------------------------------------------

LIVENESS_CASES = [
    ("is_rtsp_recording", _rtsp_backend, "H264Encoder"),
    ("is_mjpeg_streaming", _mjpeg_backend, "MJPEGEncoder"),
]


def test_stopping_rtsp_recording_twice_is_a_no_op() -> None:
    """The second stop must not re-enter stop_encoder, which now raises."""
    backend, picam, modules = _rtsp_backend()

    backend.stop_rtsp_recording()
    backend.stop_rtsp_recording(clear_url=True)

    picam.stop_encoder.assert_called_once_with(_encoder_of(modules, "H264Encoder"))
    assert backend.is_rtsp_recording is False


def test_stopping_a_dead_h264_encoder_is_swallowed(caplog) -> None:
    """Encoder.stop() raises 'Encoder already stopped' when it died on its own."""
    backend, picam, modules = _rtsp_backend()
    encoder = _encoder_of(modules, "H264Encoder")
    encoder.running = False  # died on its own, still referenced by the backend

    with caplog.at_level(logging.DEBUG):
        backend.stop_rtsp_recording()

    picam.stop_encoder.assert_called_once_with(encoder)
    assert "H264 encoder was already detached" in caplog.text
    assert backend._h264_encoder is None
    assert backend.is_rtsp_recording is False


@pytest.mark.parametrize("prop, make_backend, encoder_class", LIVENESS_CASES)
def test_liveness_is_true_while_the_encoder_runs_on_a_started_camera(
    prop, make_backend, encoder_class
) -> None:
    backend, picam, modules = make_backend()
    encoder = _encoder_of(modules, encoder_class)

    assert picam.started is True
    assert encoder in picam.encoders
    assert encoder.running is True
    assert getattr(backend, prop) is True


@pytest.mark.parametrize("prop, make_backend, encoder_class", LIVENESS_CASES)
def test_liveness_is_false_when_the_camera_is_not_started(
    prop, make_backend, encoder_class
) -> None:
    """start_encoder() on a stopped camera succeeds but nothing is dispatched."""
    backend, picam, _modules = make_backend()
    picam.started = False

    assert getattr(backend, prop) is False


@pytest.mark.parametrize("prop, make_backend, encoder_class", LIVENESS_CASES)
def test_liveness_is_false_when_the_encoder_was_detached(prop, make_backend, encoder_class) -> None:
    """Only encoders in picam.encoders are fed by the dispatch loop."""
    backend, picam, modules = make_backend()
    encoder = _encoder_of(modules, encoder_class)
    picam.encoders.discard(encoder)

    assert encoder.running is True  # the flag alone is not enough
    assert getattr(backend, prop) is False


@pytest.mark.parametrize("prop, make_backend, encoder_class", LIVENESS_CASES)
def test_liveness_is_false_inside_the_stop_encoder_window(
    prop, make_backend, encoder_class
) -> None:
    """stop_encoder() clears `running` before it removes the encoder from the set."""
    backend, picam, modules = make_backend()
    encoder = _encoder_of(modules, encoder_class)
    encoder.running = False

    assert encoder in picam.encoders  # membership alone is not enough
    assert getattr(backend, prop) is False


@pytest.mark.parametrize("prop, make_backend, encoder_class", LIVENESS_CASES)
def test_liveness_is_false_when_encoders_is_not_a_container(
    prop, make_backend, encoder_class
) -> None:
    """`encoder in encoders` raises TypeError on a non-container; assume detached."""
    backend, picam, _modules = make_backend()
    picam.encoders = Mock()

    assert getattr(backend, prop) is False


def test_rtsp_liveness_is_false_when_the_ffmpeg_output_is_broken() -> None:
    """A dead ffmpeg subprocess drops frames while the encoder still reports running."""
    backend, picam, modules = _rtsp_backend()
    encoder = _encoder_of(modules, "H264Encoder")
    output = modules["picamera2.outputs"].FfmpegOutput.return_value

    output.output_broken = True

    assert encoder in picam.encoders and encoder.running is True
    assert backend._h264_output is output
    assert backend.is_rtsp_recording is False


def test_liveness_is_false_for_an_unwired_mock_camera() -> None:
    """
    Regression guard for the `recording` bug.

    A Mock camera answers every attribute with a truthy Mock. Both properties must
    still report False rather than trading always-False-by-accident for
    always-True-by-accident.
    """
    backend = _backend()
    backend._picam = Mock()
    backend._running = True
    backend._h264_encoder = Mock()
    backend._h264_output = Mock()
    backend._mjpeg_encoder = Mock()

    assert backend.is_rtsp_recording is False
    assert backend.is_mjpeg_streaming is False


def test_liveness_is_false_without_a_camera() -> None:
    backend = _backend()

    assert backend.is_rtsp_recording is False
    assert backend.is_mjpeg_streaming is False
