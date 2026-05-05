"""
RTSP streaming via MediaMTX (formerly rtsp-simple-server).

Architecture:
  picamera2 / V4L2 → ffmpeg → publish to MediaMTX → RTSP clients
                                                     → HLS clients
                                                     → WebRTC clients

MediaMTX is a standalone binary (~10 MB). The install script downloads it.
ffmpeg reads from the V4L2 device (or stdin pipe from picamera2) and
publishes to MediaMTX via RTSP re-publish (rtsps://localhost/cam).
"""

import logging
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

MEDIAMTX_DEFAULT_PATH = Path("/usr/local/bin/mediamtx")
MEDIAMTX_CONFIG_PATH = Path("/etc/bambucam/mediamtx.yml")

# Public ports exposed to clients
RTSP_PORT = 8554
HLS_PORT = 8888
WEBRTC_PORT = 8889

# Internal port ffmpeg publishes to (not exposed)
_INTERNAL_RTSP_PORT = 8555


class RTSPStreamer:
    """
    Manages MediaMTX + stream publisher for RTSP/HLS streaming.

    Two publisher modes (selected automatically):
      - picamera2 backend: uses H264Encoder + FfmpegOutput inside picamera2
        to avoid the V4L2 device conflict (picamera2 holds /dev/videoN
        exclusively; ffmpeg cannot open it concurrently).
      - V4L2 backend / USB webcam: ffmpeg reads directly from the V4L2
        device and publishes to MediaMTX via RTSP.

    Both modes publish to rtsp://localhost:{RTSP_PORT}/{stream_name}.
    MediaMTX distributes the stream to RTSP/HLS/WebRTC clients.
    """

    def __init__(
        self,
        v4l2_device: str,
        resolution: str = "1920x1080",
        framerate: int = 15,
        bitrate_kbps: int = 2000,
        stream_name: str = "cam",
        mediamtx_path: Path = MEDIAMTX_DEFAULT_PATH,
        enable_hls: bool = True,
        enable_webrtc: bool = False,
        rtsp_auth_user: Optional[str] = None,
        rtsp_auth_pass: Optional[str] = None,
        camera_backend=None,  # Picamera2Backend instance → use H264Encoder mode
    ):
        self._device = v4l2_device
        self._resolution = resolution
        self._framerate = framerate
        self._bitrate = bitrate_kbps
        self._stream_name = stream_name
        self._mediamtx_path = mediamtx_path
        self._enable_hls = enable_hls
        self._enable_webrtc = enable_webrtc
        self._auth_user = rtsp_auth_user
        self._auth_pass = rtsp_auth_pass
        self._camera_backend = camera_backend

        self._mediamtx_proc: Optional[subprocess.Popen] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._config_file: Optional[Path] = None
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None

    # ---------------------------------------------------------------------------
    # Publisher mode helpers
    # ---------------------------------------------------------------------------

    def _uses_picamera2(self) -> bool:
        """True when using picamera2 H264Encoder instead of ffmpeg + V4L2."""
        return self._camera_backend is not None and hasattr(
            self._camera_backend, "start_rtsp_recording"
        )

    def _publish_url(self) -> str:
        return f"rtsp://localhost:{RTSP_PORT}/{self._stream_name}"

    def _start_publisher(self) -> None:
        if self._uses_picamera2():
            log.info("RTSP publisher: picamera2 H264Encoder → %s", self._publish_url())
            self._camera_backend.start_rtsp_recording(self._publish_url(), self._bitrate)
        else:
            log.info("RTSP publisher: ffmpeg V4L2 → %s", self._publish_url())
            self._start_ffmpeg()

    def _stop_publisher(self) -> None:
        if self._uses_picamera2():
            self._camera_backend.stop_rtsp_recording()
        else:
            self._kill(self._ffmpeg_proc, "ffmpeg")
            self._ffmpeg_proc = None

    def _publisher_alive(self) -> bool:
        if self._uses_picamera2():
            return self._camera_backend.is_rtsp_recording
        return self._ffmpeg_proc is not None and self._ffmpeg_proc.poll() is None

    # ---------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return

        if not self._mediamtx_path.exists():
            raise FileNotFoundError(
                f"MediaMTX not found at {self._mediamtx_path}. "
                "Run the BambuCam installer or download it manually."
            )

        self._config_file = self._write_mediamtx_config()
        self._start_mediamtx()
        time.sleep(1.0)  # Give MediaMTX time to bind ports
        self._start_publisher()
        self._running = True

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="rtsp-monitor"
        )
        self._monitor_thread.start()
        log.info(
            "RTSP stream available at rtsp://localhost:%d/%s",
            RTSP_PORT,
            self._stream_name,
        )

    def stop(self) -> None:
        self._running = False
        self._stop_publisher()
        self._kill(self._mediamtx_proc, "mediamtx")
        self._mediamtx_proc = None
        if self._config_file and self._config_file.exists():
            try:
                self._config_file.unlink()
            except OSError:
                pass
        log.info("RTSP streamer stopped")

    def update_settings(
        self,
        resolution: Optional[str] = None,
        framerate: Optional[int] = None,
        bitrate_kbps: Optional[int] = None,
    ) -> None:
        """Restart the stream publisher with new parameters."""
        if resolution:
            self._resolution = resolution
        if framerate:
            self._framerate = framerate
        if bitrate_kbps:
            self._bitrate = bitrate_kbps
        if self._running:
            self._stop_publisher()
            time.sleep(0.5)
            self._start_publisher()

    # ---------------------------------------------------------------------------
    # MediaMTX configuration
    # ---------------------------------------------------------------------------

    def _write_mediamtx_config(self) -> Path:
        """Write a MediaMTX config file and return its path."""
        config: dict = {
            "logLevel": "warn",
            "rtspAddress": f":{RTSP_PORT}",
            "hlsAddress": f":{HLS_PORT}",
            "webrtcAddress": f":{WEBRTC_PORT}",
            "hlsAlwaysRemux": self._enable_hls,
            "webrtcICEServers2": [],
            "paths": {
                self._stream_name: {
                    "source": "publisher",
                }
            },
        }

        if self._auth_user and self._auth_pass:
            config["authMethod"] = "internal"
            config["authInternalUsers"] = [
                {
                    "user": self._auth_user,
                    "pass": self._auth_pass,
                    "permissions": [{"action": "read"}, {"action": "publish"}],
                }
            ]

        config_path = Path(tempfile.mktemp(suffix=".yml", prefix="bambucam_mediamtx_"))
        config_path.write_text(yaml.safe_dump(config))
        log.debug("MediaMTX config written to %s", config_path)
        return config_path

    # ---------------------------------------------------------------------------
    # Process management
    # ---------------------------------------------------------------------------

    def _start_mediamtx(self) -> None:
        log.info("Starting MediaMTX from %s", self._mediamtx_path)
        self._mediamtx_proc = subprocess.Popen(
            [str(self._mediamtx_path), str(self._config_file)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _start_ffmpeg(self) -> None:
        if "x" not in self._resolution:
            raise ValueError(f"Invalid resolution format: {self._resolution!r} (expected WxH)")
        cmd = [
            "ffmpeg",
            "-loglevel",
            "warning",
            # Input: V4L2 device
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
            # Video encoding
            "-c:v",
            "libx264",
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
            str(self._framerate * 2),  # keyframe every 2s
            # Output: RTSP to MediaMTX
            "-f",
            "rtsp",
            f"rtsp://localhost:{RTSP_PORT}/{self._stream_name}",
        ]
        log.info("Starting ffmpeg: %s", " ".join(cmd))
        self._ffmpeg_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _kill(self, proc: Optional[subprocess.Popen], name: str) -> None:
        if proc is None:
            return
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        except ProcessLookupError:
            pass
        log.debug("%s terminated", name)

    def _monitor_loop(self) -> None:
        """Restart the stream publisher if it crashes unexpectedly."""
        while self._running:
            time.sleep(3)
            if not self._publisher_alive():
                log.warning("RTSP publisher stopped unexpectedly, restarting…")
                time.sleep(2)
                if self._running:
                    try:
                        self._start_publisher()
                    except Exception as e:
                        log.error("Failed to restart RTSP publisher: %s", e)

    # ---------------------------------------------------------------------------
    # Introspection
    # ---------------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    def stream_urls(self, host: str = "localhost") -> dict:
        urls: dict = {
            "rtsp": f"rtsp://{host}:{RTSP_PORT}/{self._stream_name}",
        }
        if self._enable_hls:
            urls["hls"] = f"http://{host}:{HLS_PORT}/{self._stream_name}/index.m3u8"
        if self._enable_webrtc:
            urls["webrtc"] = f"http://{host}:{WEBRTC_PORT}/{self._stream_name}"
        return urls

    def status(self) -> dict:
        mediamtx_ok = self._mediamtx_proc is not None and self._mediamtx_proc.poll() is None
        ffmpeg_ok = self._ffmpeg_proc is not None and self._ffmpeg_proc.poll() is None
        return {
            "running": self._running,
            "mediamtx_running": mediamtx_ok,
            "ffmpeg_running": ffmpeg_ok,
            "device": self._device,
            "resolution": self._resolution,
            "framerate": self._framerate,
            "bitrate_kbps": self._bitrate,
            "stream_name": self._stream_name,
            "rtsp_port": RTSP_PORT,
            "hls_port": HLS_PORT if self._enable_hls else None,
            "webrtc_port": WEBRTC_PORT if self._enable_webrtc else None,
        }
