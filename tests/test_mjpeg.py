"""Tests for the MJPEG streamer's client gate (idle capture pausing)."""

import threading
import time

import pytest

from bambucam.streaming.mjpeg import MJPEGStreamer

# Everything here runs at a high frame rate with a tiny grace period so the whole
# module stays well below two seconds of wall time.
_FAST_FPS = 100
_GRACE = 0.05
_DEADLINE = 1.0


class FakeCapture:
    """Capture function stand-in that counts invocations and returns unique frames."""

    def __init__(self):
        self._lock = threading.Lock()
        self.calls = 0

    def __call__(self) -> bytes:
        with self._lock:
            self.calls += 1
            return b"frame-%d" % self.calls

    @property
    def count(self) -> int:
        with self._lock:
            return self.calls


class FakeClient:
    """Drains generate() from a background thread, the way Flask's server does."""

    def __init__(self, streamer: MJPEGStreamer):
        self._gen = streamer.generate()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._chunks = []
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            for chunk in self._gen:
                with self._lock:
                    self._chunks.append(chunk)
                if self._stop.is_set():
                    break
        finally:
            self._gen.close()

    @property
    def chunks(self) -> list:
        with self._lock:
            return list(self._chunks)

    def wait_for_chunks(self, count: int, timeout: float = _DEADLINE) -> bool:
        return _wait_until(lambda: len(self.chunks) >= count, timeout)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=_DEADLINE)


def _wait_until(predicate, timeout: float = _DEADLINE) -> bool:
    """Poll a predicate instead of sleeping for a fixed duration."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


@pytest.fixture
def streamer():
    capture = FakeCapture()
    streamer = MJPEGStreamer(capture, target_fps=_FAST_FPS)
    streamer.IDLE_GRACE_SECONDS = _GRACE  # instance override of the class default
    streamer.capture = capture
    try:
        yield streamer
    finally:
        streamer.stop()


def test_default_grace_period_is_two_seconds():
    assert MJPEGStreamer.IDLE_GRACE_SECONDS == 2.0


def test_capture_stops_without_clients(streamer):
    streamer.start()
    assert _wait_until(lambda: streamer.idle)

    calls = streamer.capture.count
    time.sleep(0.1)
    assert streamer.capture.count == calls
    assert streamer.client_count == 0
    assert streamer.is_running is True


def test_actual_fps_is_none_while_idle(streamer):
    streamer.start()
    client = FakeClient(streamer)
    assert client.wait_for_chunks(3)
    assert streamer.actual_fps is not None
    client.close()

    # No stale rate is reported once capture pauses.
    assert _wait_until(lambda: streamer.idle)
    assert streamer.actual_fps is None


def test_stale_frame_is_dropped_when_capture_pauses(streamer):
    """An idle streamer may sit for hours — it must not hand out its last frame."""
    streamer.start()
    client = FakeClient(streamer)
    assert client.wait_for_chunks(2)
    client.close()

    assert _wait_until(lambda: streamer.idle)
    assert streamer._latest_frame is None


def test_client_on_idle_streamer_receives_fresh_frames(streamer):
    streamer.start()
    assert _wait_until(lambda: streamer.idle)
    calls_while_idle = streamer.capture.count

    client = FakeClient(streamer)
    try:
        # Two chunks: the second one can only exist if capture actually resumed.
        assert client.wait_for_chunks(2), "idle streamer never delivered frames"
        assert streamer.capture.count > calls_while_idle
        assert streamer.idle is False
        assert _wait_until(lambda: streamer.client_count == 1)

        chunk = client.chunks[0]
        assert chunk.startswith(b"--bambucam_frame\r\n")
        assert b"Content-Type: image/jpeg" in chunk
        assert chunk.endswith(b"\r\n")
    finally:
        client.close()


def test_client_receives_frames_when_no_frame_was_ever_captured(streamer):
    """The pending-client hand-off must work without a stale frame to fall back on."""
    streamer.start()
    assert _wait_until(lambda: streamer.idle)
    streamer._latest_frame = None  # simulate a streamer that never produced a frame

    client = FakeClient(streamer)
    try:
        assert client.wait_for_chunks(1), "pending client did not wake the capture loop"
        assert _wait_until(lambda: streamer.client_count == 1)
    finally:
        client.close()


def test_capture_continues_while_client_connected(streamer):
    streamer.start()
    client = FakeClient(streamer)
    try:
        assert client.wait_for_chunks(2)
        # Far longer than the grace period — a connected client must never trigger idling.
        calls = streamer.capture.count
        time.sleep(_GRACE * 4)
        assert streamer.capture.count > calls
        assert streamer.idle is False
        assert streamer.actual_fps is not None
    finally:
        client.close()


def test_grace_period_survives_a_brief_disconnect():
    capture = FakeCapture()
    streamer = MJPEGStreamer(capture, target_fps=_FAST_FPS)
    streamer.IDLE_GRACE_SECONDS = 0.4
    try:
        streamer.start()
        client = FakeClient(streamer)
        assert client.wait_for_chunks(2)
        client.close()
        assert _wait_until(lambda: streamer.client_count == 0)

        # Right after the disconnect the loop must still be capturing.
        calls = capture.count
        time.sleep(0.1)
        assert capture.count > calls
        assert streamer.idle is False

        # A reconnect within the grace period keeps it that way.
        client = FakeClient(streamer)
        try:
            assert client.wait_for_chunks(2)
            assert streamer.idle is False
        finally:
            client.close()
    finally:
        streamer.stop()


def test_stop_terminates_the_thread_while_idle(streamer):
    streamer.start()
    thread = streamer._capture_thread
    assert _wait_until(lambda: streamer.idle)

    streamer.stop()
    assert thread.is_alive() is False
    assert streamer.is_running is False
    assert streamer.idle is False


def test_stop_terminates_the_thread_while_streaming(streamer):
    streamer.start()
    thread = streamer._capture_thread
    client = FakeClient(streamer)
    try:
        assert client.wait_for_chunks(1)
        streamer.stop()
        assert thread.is_alive() is False
        assert streamer.is_running is False
    finally:
        client.close()


def test_generate_releases_pending_clients_that_never_got_a_frame(streamer):
    """A probe that disconnects before frame one must not keep the loop awake forever."""
    streamer.start()
    assert _wait_until(lambda: streamer.idle)
    streamer._latest_frame = None

    gen = streamer.generate()
    next(gen)  # returns the first frame after capture resumed
    gen.close()

    assert streamer.client_count == 0
    assert streamer._waiting_count == 0
    assert _wait_until(lambda: streamer.idle), "loop never went idle again"
