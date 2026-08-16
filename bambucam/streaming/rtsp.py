"""RTSP, HLS, and WebRTC streaming through MediaMTX."""

import logging
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

import yaml

log = logging.getLogger(__name__)

MEDIAMTX_DEFAULT_PATH = Path("/usr/local/bin/mediamtx")
RTSP_PORT = 8554
HLS_PORT = 8888
WEBRTC_PORT = 8889

SOFTWARE_ENCODER = "libx264"
# V4L2 stateful H.264 encoder: Raspberry Pi up to and including the Pi 4 / CM4
# expose one through /dev/video11. The Pi 5 dropped it, so this is detected
# rather than assumed.
HARDWARE_ENCODER = "h264_v4l2m2m"
ENCODER_PROBE_TIMEOUT = 3.0
# ffmpeg's v4l2_m2m wrapper defaults to 16 output and 4 capture buffers, which
# is too tight for 1080p live capture and shows up as dropped frames or a
# deadlocked device. Raising both is the standard Raspberry Pi recommendation.
HW_OUTPUT_BUFFERS = 32
HW_CAPTURE_BUFFERS = 16


class RTSPStreamer:
    """Manage MediaMTX and one camera publisher process."""

    # Circuit breaker for publisher crash loops: more than
    # _RESTART_BACKOFF_THRESHOLD restarts inside _RESTART_BACKOFF_WINDOW
    # seconds means the publisher cannot cope with the configured settings,
    # so back off instead of respawning encoders every few seconds.
    _RESTART_BACKOFF_THRESHOLD = 5
    _RESTART_BACKOFF_WINDOW = 60.0
    _RESTART_BACKOFF_DELAY = 30.0

    def __init__(
        self,
        v4l2_device: str,
        resolution: str = "1920x1080",
        framerate: int = 15,
        bitrate_kbps: int = 2000,
        stream_name: str = "cam",
        mediamtx_path: Path = MEDIAMTX_DEFAULT_PATH,
        ffmpeg_path: str = "ffmpeg",
        rtsp_port: int = RTSP_PORT,
        hls_port: int = HLS_PORT,
        webrtc_port: int = WEBRTC_PORT,
        enable_hls: bool = True,
        enable_webrtc: bool = False,
        rtsp_auth_user: Optional[str] = None,
        rtsp_auth_pass: Optional[str] = None,
        camera_backend=None,
        capture_fn: Optional[Callable[[], bytes]] = None,
        hw_encoder: Optional[bool] = None,
    ):
        self._device = v4l2_device
        self._resolution = resolution
        self._framerate = int(framerate)
        self._bitrate = int(bitrate_kbps)
        self._stream_name = stream_name
        self._mediamtx_path = Path(mediamtx_path)
        self._ffmpeg_path = str(ffmpeg_path)
        self._rtsp_port = int(rtsp_port)
        self._hls_port = int(hls_port)
        self._webrtc_port = int(webrtc_port)
        self._enable_hls = bool(enable_hls)
        self._enable_webrtc = bool(enable_webrtc)
        self._auth_user = rtsp_auth_user or None
        self._auth_pass = rtsp_auth_pass or None
        self._camera_backend = camera_backend
        self._capture_fn = capture_fn
        # None = auto-detect, False = always libx264, True = force h264_v4l2m2m.
        self._hw_encoder_pref = hw_encoder
        self._hw_encoder_available: Optional[bool] = None
        self._encoder_logged = False

        self._mediamtx_proc: Optional[subprocess.Popen] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._config_file: Optional[Path] = None
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._feeder_thread: Optional[threading.Thread] = None
        self._feeder_running = False
        self._lock = threading.RLock()
        self._restart_timestamps: list[float] = []

        self._validate_settings()

    def _validate_settings(self) -> None:
        for name, value in (
            ("RTSP", self._rtsp_port),
            ("HLS", self._hls_port),
            ("WebRTC", self._webrtc_port),
        ):
            if not 1 <= int(value) <= 65535:
                raise ValueError(f"{name} port must be between 1 and 65535")
        if len({self._rtsp_port, self._hls_port, self._webrtc_port}) != 3:
            raise ValueError("RTSP, HLS, and WebRTC ports must be different")
        if not self._stream_name or any(char in self._stream_name for char in " /?#"):
            raise ValueError("Invalid RTSP stream name")
        if self._framerate < 1:
            raise ValueError("RTSP framerate must be at least 1")
        if self._bitrate < 100:
            raise ValueError("RTSP bitrate must be at least 100 kbps")

    def _uses_picamera2(self) -> bool:
        return self._camera_backend is not None and hasattr(
            self._camera_backend, "start_rtsp_recording"
        )

    def _uses_frame_pipe(self) -> bool:
        return not self._uses_picamera2() and self._capture_fn is not None

    def _publish_url(self, include_credentials: bool = True) -> str:
        credentials = ""
        if include_credentials and self._auth_user and self._auth_pass:
            credentials = f"{quote(self._auth_user, safe='')}:{quote(self._auth_pass, safe='')}@"
        return f"rtsp://{credentials}127.0.0.1:{self._rtsp_port}/{self._stream_name}"

    def _start_publisher(self) -> None:
        if self._uses_picamera2():
            log.info(
                "RTSP publisher: picamera2 H264Encoder → %s",
                self._publish_url(include_credentials=False),
            )
            self._camera_backend.start_rtsp_recording(self._publish_url(), self._bitrate)
        else:
            mode = "JPEG frame pipe" if self._uses_frame_pipe() else f"V4L2 {self._device}"
            log.info("RTSP publisher: ffmpeg %s", mode)
            self._start_ffmpeg()

    def _stop_publisher(self, clear_url: bool = True) -> None:
        if self._uses_picamera2():
            self._camera_backend.stop_rtsp_recording(clear_url=clear_url)
            return

        self._feeder_running = False
        process = self._ffmpeg_proc
        if process is not None and process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if (
            self._feeder_thread is not None
            and self._feeder_thread is not threading.current_thread()
        ):
            self._feeder_thread.join(timeout=3)
        self._feeder_thread = None
        self._kill(process, "ffmpeg")
        self._ffmpeg_proc = None

    def _publisher_crash_looping(self) -> bool:
        """Drop restarts outside the window and report whether we are in a crash loop."""
        cutoff = time.monotonic() - self._RESTART_BACKOFF_WINDOW
        self._restart_timestamps = [ts for ts in self._restart_timestamps if ts > cutoff]
        return len(self._restart_timestamps) >= self._RESTART_BACKOFF_THRESHOLD

    def _publisher_alive(self) -> bool:
        if self._uses_picamera2():
            return bool(self._camera_backend.is_rtsp_recording)
        return self._ffmpeg_proc is not None and self._ffmpeg_proc.poll() is None

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            if not self._mediamtx_path.exists():
                raise FileNotFoundError(
                    f"MediaMTX not found at {self._mediamtx_path}. Run the BambuCam installer."
                )

            self._config_file = self._write_mediamtx_config()
            try:
                self._start_mediamtx()
                self._wait_for_port(self._rtsp_port)
                self._start_publisher()
            except Exception:
                self._stop_publisher()
                self._kill(self._mediamtx_proc, "mediamtx")
                self._mediamtx_proc = None
                self._remove_config_file()
                raise

            self._running = True
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop,
                daemon=True,
                name="rtsp-monitor",
            )
            self._monitor_thread.start()
            log.info(
                "RTSP stream available at rtsp://localhost:%d/%s",
                self._rtsp_port,
                self._stream_name,
            )

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._stop_publisher()
            self._kill(self._mediamtx_proc, "mediamtx")
            self._mediamtx_proc = None
            self._remove_config_file()
        if (
            self._monitor_thread is not None
            and self._monitor_thread is not threading.current_thread()
        ):
            self._monitor_thread.join(timeout=4)
        self._monitor_thread = None
        log.info("RTSP streamer stopped")

    def update_settings(
        self,
        resolution: Optional[str] = None,
        framerate: Optional[int] = None,
        bitrate_kbps: Optional[int] = None,
        stream_name: Optional[str] = None,
        rtsp_port: Optional[int] = None,
        hls_port: Optional[int] = None,
        webrtc_port: Optional[int] = None,
        enable_hls: Optional[bool] = None,
        enable_webrtc: Optional[bool] = None,
    ) -> None:
        """Apply publisher settings and restart MediaMTX when network settings change."""
        with self._lock:
            server_restart = any(
                value is not None
                for value in (
                    stream_name,
                    rtsp_port,
                    hls_port,
                    webrtc_port,
                    enable_hls,
                    enable_webrtc,
                )
            )
            was_running = self._running
            if was_running and server_restart:
                self.stop()

            if resolution is not None:
                self._resolution = str(resolution)
            if framerate is not None:
                self._framerate = int(framerate)
            if bitrate_kbps is not None:
                self._bitrate = int(bitrate_kbps)
            if stream_name is not None:
                self._stream_name = str(stream_name)
            if rtsp_port is not None:
                self._rtsp_port = int(rtsp_port)
            if hls_port is not None:
                self._hls_port = int(hls_port)
            if webrtc_port is not None:
                self._webrtc_port = int(webrtc_port)
            if enable_hls is not None:
                self._enable_hls = bool(enable_hls)
            if enable_webrtc is not None:
                self._enable_webrtc = bool(enable_webrtc)

            self._validate_settings()
            if was_running and server_restart:
                self.start()
            elif was_running:
                self._stop_publisher(clear_url=False)
                time.sleep(0.2)
                self._start_publisher()

    def _write_mediamtx_config(self) -> Path:
        config: dict = {
            "logLevel": "warn",
            "rtspAddress": f":{self._rtsp_port}",
            "hls": self._enable_hls,
            "hlsAddress": f":{self._hls_port}",
            "hlsAlwaysRemux": self._enable_hls,
            "webrtc": self._enable_webrtc,
            "webrtcAddress": f":{self._webrtc_port}",
            "webrtcICEServers2": [],
            "paths": {self._stream_name: {"source": "publisher"}},
        }

        if self._auth_user and self._auth_pass:
            config["authMethod"] = "internal"
            config["authInternalUsers"] = [
                {
                    "user": self._auth_user,
                    "pass": self._auth_pass,
                    "permissions": [
                        {"action": "read", "path": self._stream_name},
                        {"action": "publish", "path": self._stream_name},
                    ],
                }
            ]

        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".yml",
            prefix="bambucam_mediamtx_",
            delete=False,
        )
        try:
            yaml.safe_dump(config, handle, sort_keys=False)
            path = Path(handle.name)
        finally:
            handle.close()
        os.chmod(path, 0o600)
        log.debug("MediaMTX config written to %s", path)
        return path

    def _remove_config_file(self) -> None:
        if self._config_file and self._config_file.exists():
            try:
                self._config_file.unlink()
            except OSError:
                pass
        self._config_file = None

    def _start_mediamtx(self) -> None:
        log.info("Starting MediaMTX from %s", self._mediamtx_path)
        self._mediamtx_proc = subprocess.Popen(
            [str(self._mediamtx_path), str(self._config_file)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @staticmethod
    def _wait_for_port(port: int, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError(f"MediaMTX did not bind RTSP port {port} within {timeout:.1f}s")

    def _hardware_encoder_available(self) -> bool:
        """Report whether the configured ffmpeg binary was built with h264_v4l2m2m.

        The probe shells out once per streamer and the answer is cached: the
        publisher crash-restart path calls _start_ffmpeg() repeatedly and must
        not spawn an ffmpeg probe every time. A missing binary, a non-zero exit,
        a timeout, or any other OS error all mean "not available".

        Caveat: ffmpeg listing the encoder is necessary but not sufficient. The
        V4L2 device may be absent (Pi 5), busy, or refuse the requested format,
        and that only shows up when the publisher actually starts. Probing the
        device here would compete with the running publisher for it, so we do
        not; users hitting that can pass hw_encoder=False to force libx264.
        """
        if self._hw_encoder_available is not None:
            return self._hw_encoder_available

        available = False
        try:
            result = subprocess.run(
                [self._ffmpeg_path, "-hide_banner", "-encoders"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=ENCODER_PROBE_TIMEOUT,
                check=False,
            )
            if result.returncode == 0:
                output = result.stdout or b""
                if isinstance(output, bytes):
                    output = output.decode("utf-8", "replace")
                available = HARDWARE_ENCODER in output
        except Exception as exc:
            # Missing binary, non-zero exit, timeout, OSError — every failure
            # mode means "assume no hardware encoder" and never propagates:
            # the probe must not be able to stop the publisher from starting.
            log.debug("ffmpeg encoder probe failed: %s", exc)
            available = False

        self._hw_encoder_available = available
        return available

    def _select_encoder(self) -> str:
        """Pick the video encoder, logging the choice and its reason once."""
        if self._hw_encoder_pref is False:
            encoder, reason = SOFTWARE_ENCODER, "hardware encoding disabled by configuration"
        elif self._hw_encoder_pref is True:
            encoder, reason = HARDWARE_ENCODER, "forced by configuration"
        elif self._hardware_encoder_available():
            encoder, reason = HARDWARE_ENCODER, f"advertised by {self._ffmpeg_path}"
        else:
            encoder, reason = SOFTWARE_ENCODER, f"{HARDWARE_ENCODER} unavailable in ffmpeg"

        if not self._encoder_logged:
            log.info("RTSP video encoder: %s (%s)", encoder, reason)
            self._encoder_logged = True
        return encoder

    def _encoder_args(self) -> list[str]:
        keyframe_interval = str(self._framerate * 2)
        encoder = self._select_encoder()
        if encoder == HARDWARE_ENCODER:
            # -preset/-tune are libx264-private options; ffmpeg aborts if they
            # reach h264_v4l2m2m. The V4L2 wrapper only forwards bitrate and GOP
            # size to the driver, so -maxrate/-bufsize would be dead weight.
            return [
                "-c:v",
                HARDWARE_ENCODER,
                "-b:v",
                f"{self._bitrate}k",
                "-num_output_buffers",
                str(HW_OUTPUT_BUFFERS),
                "-num_capture_buffers",
                str(HW_CAPTURE_BUFFERS),
                "-pix_fmt",
                "yuv420p",
                "-g",
                keyframe_interval,
            ]
        return [
            "-c:v",
            SOFTWARE_ENCODER,
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-b:v",
            f"{self._bitrate}k",
            "-maxrate",
            f"{self._bitrate}k",
            "-bufsize",
            f"{self._bitrate * 2}k",
            "-pix_fmt",
            "yuv420p",
            "-g",
            keyframe_interval,
        ]

    def _start_ffmpeg(self) -> None:
        if "x" not in self._resolution:
            raise ValueError(f"Invalid resolution format: {self._resolution!r} (expected WxH)")

        command = [self._ffmpeg_path, "-hide_banner", "-loglevel", "warning"]
        if self._uses_frame_pipe():
            command.extend(
                [
                    "-f",
                    "mjpeg",
                    "-framerate",
                    str(self._framerate),
                    "-i",
                    "pipe:0",
                ]
            )
        else:
            command.extend(
                [
                    "-f",
                    "v4l2",
                    "-input_format",
                    "mjpeg",
                    "-video_size",
                    self._resolution,
                    "-framerate",
                    str(self._framerate),
                    "-i",
                    self._device,
                ]
            )

        command.append("-an")
        command.extend(self._encoder_args())
        command.extend(
            [
                "-f",
                "rtsp",
                "-rtsp_transport",
                "tcp",
                self._publish_url(),
            ]
        )
        log.debug("Starting ffmpeg publisher: %s", " ".join(command[:-1] + ["<publish-url>"]))
        self._ffmpeg_proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if self._uses_frame_pipe() else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

        if self._uses_frame_pipe():
            self._feeder_running = True
            self._feeder_thread = threading.Thread(
                target=self._frame_feeder_loop,
                daemon=True,
                name="rtsp-frame-feeder",
            )
            self._feeder_thread.start()

    def _frame_feeder_loop(self) -> None:
        interval = 1.0 / max(1, self._framerate)
        while self._feeder_running:
            started = time.monotonic()
            process = self._ffmpeg_proc
            if process is None or process.poll() is not None or process.stdin is None:
                break
            try:
                frame = self._capture_fn()
                if frame:
                    process.stdin.write(frame)
                    process.stdin.flush()
            except (BrokenPipeError, OSError):
                break
            except Exception as exc:
                log.warning("RTSP frame capture failed: %s", exc)
                time.sleep(0.5)
                continue

            remaining = interval - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
        self._feeder_running = False

    @staticmethod
    def _kill(process: Optional[subprocess.Popen], name: str) -> None:
        if process is None:
            return
        try:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        except ProcessLookupError:
            pass
        log.debug("%s terminated", name)

    def _monitor_loop(self) -> None:
        while self._running:
            time.sleep(3)
            if not self._running:
                break
            if self._mediamtx_proc is None or self._mediamtx_proc.poll() is not None:
                log.error("MediaMTX stopped unexpectedly; restarting streaming stack")
                try:
                    with self._lock:
                        self._stop_publisher(clear_url=False)
                        self._start_mediamtx()
                        self._wait_for_port(self._rtsp_port)
                        self._start_publisher()
                except Exception as exc:
                    log.error("Failed to recover MediaMTX: %s", exc)
                continue
            if not self._publisher_alive():
                if self._publisher_crash_looping():
                    log.error(
                        "RTSP publisher crashed %d times in %.0fs — pausing restarts for %.0fs. "
                        "The encoder cannot keep up with the current settings; check "
                        "streaming.rtsp.bitrate_kbps, resolution, and framerate.",
                        len(self._restart_timestamps),
                        self._RESTART_BACKOFF_WINDOW,
                        self._RESTART_BACKOFF_DELAY,
                    )
                    time.sleep(self._RESTART_BACKOFF_DELAY)
                    continue
                log.warning("RTSP publisher stopped unexpectedly, restarting…")
                self._restart_timestamps.append(time.monotonic())
                try:
                    with self._lock:
                        self._stop_publisher(clear_url=False)
                        self._start_publisher()
                except Exception as exc:
                    log.error("Failed to restart RTSP publisher: %s", exc)

    @property
    def is_running(self) -> bool:
        return self._running

    def stream_urls(self, host: str = "localhost") -> dict:
        urls: dict = {"rtsp": f"rtsp://{host}:{self._rtsp_port}/{self._stream_name}"}
        if self._enable_hls:
            urls["hls"] = f"http://{host}:{self._hls_port}/{self._stream_name}/index.m3u8"
        if self._enable_webrtc:
            urls["webrtc"] = f"http://{host}:{self._webrtc_port}/{self._stream_name}"
        return urls

    def _published_resolution(self) -> str:
        """
        The resolution subscribers actually receive.

        With picamera2 the publisher encodes the camera's separate encode stream,
        which is smaller than the still-capture resolution — reporting the latter
        would tell the WebUI a resolution that never goes out over RTSP.
        """
        encode_size = getattr(self._camera_backend, "encode_resolution", None)
        if self._uses_picamera2() and encode_size:
            return f"{encode_size[0]}x{encode_size[1]}"
        return self._resolution

    def status(self) -> dict:
        mediamtx_ok = self._mediamtx_proc is not None and self._mediamtx_proc.poll() is None
        return {
            "running": self._running,
            "mediamtx_running": mediamtx_ok,
            "publisher_running": self._publisher_alive(),
            "publisher_mode": (
                "picamera2"
                if self._uses_picamera2()
                else "frame_pipe" if self._uses_frame_pipe() else "v4l2"
            ),
            "device": self._device,
            "resolution": self._published_resolution(),
            "framerate": self._framerate,
            "bitrate_kbps": self._bitrate,
            "stream_name": self._stream_name,
            "rtsp_port": self._rtsp_port,
            "hls_port": self._hls_port if self._enable_hls else None,
            "webrtc_port": self._webrtc_port if self._enable_webrtc else None,
        }
