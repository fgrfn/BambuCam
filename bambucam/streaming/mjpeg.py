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
_IDLE_WAKE_TIMEOUT = 0.5  # max seconds a paused loop sleeps before re-checking _running


class MJPEGStreamer:
    """
    Manages a pool of connected MJPEG clients.

    Architecture:
    - One capture thread reads frames from the camera backend and puts them
      into a shared slot (latest_frame).
    - Each HTTP client gets an independent generator that reads from that slot.
    - This avoids multiple concurrent camera reads and ensures all clients
      see the same frame rate without blocking each other.
    - Without clients the capture thread pauses, because JPEG encoding is the
      most expensive thing BambuCam does on a small Pi and nobody is watching.
    """

    # Seconds the loop keeps capturing after the last client left. A browser refresh or
    # a reconnecting BambuBuddy must not cause a capture stop/start storm.
    IDLE_GRACE_SECONDS = 2.0

    def __init__(
        self,
        capture_fn: Callable[[], bytes],
        target_fps: int = 15,
        on_resume: Optional[Callable[[], None]] = None,
        on_pause: Optional[Callable[[], None]] = None,
    ):
        self._capture_fn = capture_fn
        self._target_fps = target_fps
        self._frame_interval = 1.0 / target_fps
        # Optional hooks for a frame source that has to be switched on and off with
        # demand — a camera-side encoder keeps running (and costs CPU) otherwise,
        # which would defeat the idle pause below.
        self._on_resume = on_resume
        self._on_pause = on_pause

        self._latest_frame: Optional[bytes] = None
        self._frame_lock = threading.Condition()
        self._capture_thread: Optional[threading.Thread] = None
        self._running = False
        self._client_count = 0
        # Clients that are connected but have not been sent their first frame yet.
        # They keep the capture loop awake even though client_count is still 0.
        self._waiting_count = 0
        self._client_lock = threading.Lock()
        self._idle = False
        self._wake = threading.Event()
        self._frame_times: collections.deque = collections.deque(maxlen=_FPS_WINDOW)

    # ---------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._idle = False
        self._wake.clear()
        self._frame_times.clear()
        self._capture_thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="mjpeg-capture"
        )
        self._capture_thread.start()
        log.info("MJPEG capture loop started (target %d fps)", self._target_fps)

    def stop(self) -> None:
        self._running = False
        self._wake.set()  # release the loop if it is parked in the idle wait
        with self._frame_lock:
            self._frame_lock.notify_all()
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=3)
        self._idle = False
        self._notify_source(self._on_pause, "stop")
        log.info("MJPEG streamer stopped")

    def update_fps(self, fps: int) -> None:
        self._target_fps = fps
        self._frame_interval = 1.0 / fps

    # ---------------------------------------------------------------------------
    # Frame capture loop (single thread, shared by all clients)
    # ---------------------------------------------------------------------------

    def _has_clients(self) -> bool:
        """True while anyone is connected — including clients still awaiting frame one."""
        with self._client_lock:
            return (self._client_count + self._waiting_count) > 0

    def _wait_for_clients(self) -> bool:
        """
        Park the capture thread until a client shows up.
        Returns True when capture should resume, False when the streamer was stopped.
        """
        try:
            while self._running:
                if self._has_clients():
                    log.debug("MJPEG capture resumed (client connected)")
                    return True
                self._wake.wait(timeout=_IDLE_WAKE_TIMEOUT)
                # Clear before re-reading the counters: a client always bumps them
                # before it sets the event, so no wakeup can be lost this way.
                self._wake.clear()
            return False
        finally:
            self._idle = False

    def _notify_source(self, hook: Optional[Callable[[], None]], action: str) -> None:
        """Run a frame-source hook; a failing source must never kill the capture thread."""
        if hook is None:
            return
        try:
            hook()
        except Exception as exc:
            log.warning("MJPEG frame source failed to %s: %s", action, exc)

    def _capture_loop(self) -> None:
        last_seen_client = time.monotonic()
        self._notify_source(self._on_resume, "start")
        while self._running:
            if self._has_clients():
                last_seen_client = time.monotonic()
            elif time.monotonic() - last_seen_client >= self.IDLE_GRACE_SECONDS:
                self._idle = True
                self._notify_source(self._on_pause, "pause")
                # Drop the measurement window so actual_fps reports None while paused
                # instead of the rate that was current when the last client left.
                self._frame_times.clear()
                # Drop the last frame too. A streamer can stay idle for hours, and
                # the next client must not be served that stale image as if it were
                # live — it waits one frame interval and gets a current one instead.
                with self._frame_lock:
                    self._latest_frame = None
                log.debug("MJPEG capture paused (no clients)")
                if not self._wait_for_clients():
                    break
                self._notify_source(self._on_resume, "resume")
                last_seen_client = time.monotonic()

            t0 = time.monotonic()
            try:
                frame = self._capture_fn()
                # A source that yields nothing — an encoder that has not produced its
                # first frame yet — must not overwrite the last good frame. Fall through
                # to the pacing below rather than retrying immediately.
                if frame:
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
        # Announce demand before waiting for a frame. The capture loop may be paused,
        # and it would never produce the frame this client is waiting for — while the
        # client, in turn, is only counted once that frame arrives. Waiting clients
        # break that deadlock: they wake the loop and are promoted to counted ones below.
        with self._client_lock:
            self._waiting_count += 1
        self._wake.set()
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
                        self._waiting_count -= 1
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
            with self._client_lock:
                if counted:
                    self._client_count -= 1
                else:
                    self._waiting_count -= 1
            if counted:
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
    def idle(self) -> bool:
        """True while the capture loop is paused because no client is connected."""
        return self._idle

    @property
    def is_running(self) -> bool:
        return self._running
