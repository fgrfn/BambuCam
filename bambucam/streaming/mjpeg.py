"""
MJPEG streamer — serves a multipart/x-mixed-replace HTTP stream.
Clients (browsers, VLC, OBS) open the URL and receive continuous JPEG frames.
"""

import collections
import logging
import threading
import time
from collections.abc import Iterator
from typing import Callable, Optional

log = logging.getLogger(__name__)

_BOUNDARY = b"--bambucam_frame"
_CRLF = b"\r\n"
_FPS_WINDOW = 30  # number of recent frame timestamps to keep for fps measurement


class MJPEGStreamer:
    """
    Manages a pool of connected MJPEG clients.

    Architecture:
    - One capture thread reads frames from the camera backend and puts them
      into a shared slot (latest_frame).
    - Each HTTP client gets an independent generator that reads from that slot.
    - This avoids multiple concurrent camera reads and ensures all clients
      see the same frame rate without blocking each other.
    """

    def __init__(
        self,
        capture_fn: Callable[[], bytes],
        target_fps: int = 15,
    ):
        self._capture_fn = capture_fn
        self._target_fps = target_fps
        self._frame_interval = 1.0 / target_fps

        self._latest_frame: Optional[bytes] = None
        self._frame_lock = threading.Condition()
        self._capture_thread: Optional[threading.Thread] = None
        self._running = False
        self._client_count = 0
        self._client_lock = threading.Lock()
        self._frame_times: collections.deque = collections.deque(maxlen=_FPS_WINDOW)

    # ---------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._frame_times.clear()
        self._capture_thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="mjpeg-capture"
        )
        self._capture_thread.start()
        log.info("MJPEG capture loop started (target %d fps)", self._target_fps)

    def stop(self) -> None:
        self._running = False
        with self._frame_lock:
            self._frame_lock.notify_all()
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=3)
        log.info("MJPEG streamer stopped")

    def update_fps(self, fps: int) -> None:
        self._target_fps = fps
        self._frame_interval = 1.0 / fps

    # ---------------------------------------------------------------------------
    # Frame capture loop (single thread, shared by all clients)
    # ---------------------------------------------------------------------------

    def _capture_loop(self) -> None:
        while self._running:
            t0 = time.monotonic()
            try:
                frame = self._capture_fn()
                with self._frame_lock:
                    self._latest_frame = frame
                    self._frame_lock.notify_all()
                self._frame_times.append(t0)
            except Exception as e:
                log.warning("MJPEG capture error: %s", e)
                time.sleep(0.5)
                continue

            elapsed = time.monotonic() - t0
            sleep_for = self._frame_interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    # ---------------------------------------------------------------------------
    # HTTP response generator (one per connected client)
    # ---------------------------------------------------------------------------

    def generate(self) -> Iterator[bytes]:
        """
        Yield multipart chunks suitable for a Flask streaming response.
        Called once per HTTP client connection.
        """
        counted = False
        last_frame: Optional[bytes] = None
        try:
            while self._running:
                with self._frame_lock:
                    self._frame_lock.wait(timeout=2.0)
                    frame = self._latest_frame

                if frame is None or frame is last_frame:
                    continue
                last_frame = frame

                # Count only once the first real frame is about to be sent,
                # so aborted HEAD probes and abandoned connections never inflate the counter.
                if not counted:
                    with self._client_lock:
                        self._client_count += 1
                    counted = True
                    log.debug("MJPEG client connected (total: %d)", self._client_count)

                yield (
                    _BOUNDARY
                    + _CRLF
                    + b"Content-Type: image/jpeg"
                    + _CRLF
                    + b"Content-Length: "
                    + str(len(frame)).encode()
                    + _CRLF
                    + _CRLF
                    + frame
                    + _CRLF
                )
        except GeneratorExit:
            pass
        finally:
            if counted:
                with self._client_lock:
                    self._client_count -= 1
                log.debug("MJPEG client disconnected (total: %d)", self._client_count)

    @property
    def actual_fps(self) -> Optional[float]:
        """Measured capture rate based on the last up-to-30 frame timestamps."""
        times = list(self._frame_times)
        if len(times) < 2:
            return None
        return round((len(times) - 1) / (times[-1] - times[0]), 1)

    @property
    def client_count(self) -> int:
        return self._client_count

    @property
    def is_running(self) -> bool:
        return self._running
