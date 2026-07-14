"""Background timelapse capture and MP4 rendering service."""

import json
import logging
import re
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)
_SESSION_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")


@dataclass
class TimelapseStatus:
    running: bool = False
    rendering: bool = False
    session_id: str = ""
    title: str = ""
    interval_seconds: float = 10.0
    output_fps: int = 30
    frame_count: int = 0
    started_at: Optional[float] = None
    last_frame_at: Optional[float] = None
    next_frame_at: Optional[float] = None
    stopped_at: Optional[float] = None
    video_path: str = ""
    error: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class TimelapseService:
    """Capture JPEG frames on a monotonic interval and render them with ffmpeg."""

    def __init__(
        self,
        capture_fn,
        root_dir: Path = Path("/var/lib/bambucam/timelapse"),
        ffmpeg_path: str = "ffmpeg",
        interval_seconds: float = 10.0,
        output_fps: int = 30,
        max_sessions: int = 20,
        max_age_days: int = 90,
        render_on_stop: bool = True,
    ):
        self._capture_fn = capture_fn
        self._root_dir = Path(root_dir)
        self._ffmpeg_path = str(ffmpeg_path)
        self._default_interval = self._validate_interval(interval_seconds)
        self._default_output_fps = self._validate_output_fps(output_fps)
        self._max_sessions = max(0, int(max_sessions))
        self._max_age_days = max(0, int(max_age_days))
        self._render_on_stop = bool(render_on_stop)
        self._status = TimelapseStatus(
            interval_seconds=self._default_interval,
            output_fps=self._default_output_fps,
        )
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    @property
    def status(self) -> TimelapseStatus:
        with self._lock:
            return TimelapseStatus(**self._status.as_dict())

    def start(
        self,
        title: str = "",
        interval_seconds: Optional[float] = None,
        output_fps: Optional[int] = None,
    ) -> TimelapseStatus:
        """Start a new timelapse session and capture the first frame immediately."""
        interval = self._validate_interval(
            self._default_interval if interval_seconds is None else interval_seconds
        )
        fps = self._validate_output_fps(
            self._default_output_fps if output_fps is None else output_fps
        )
        with self._lock:
            if self._status.running or self._status.rendering:
                raise RuntimeError("A timelapse session is already active")

            session_id = self._new_session_id()
            session_dir = self._session_dir(session_id)
            (session_dir / "frames").mkdir(parents=True, exist_ok=False)
            started = time.time()
            self._status = TimelapseStatus(
                running=True,
                session_id=session_id,
                title=str(title).strip()[:160],
                interval_seconds=interval,
                output_fps=fps,
                started_at=started,
                next_frame_at=started,
            )
            self._write_metadata_locked()
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._capture_loop,
                daemon=True,
                name="timelapse-capture",
            )
            self._thread.start()
            log.info("Timelapse session %s started at %.2fs interval", session_id, interval)
            return self.status

    def stop(self, render: Optional[bool] = None) -> TimelapseStatus:
        """Stop capture and optionally render the completed session."""
        with self._lock:
            if not self._status.running:
                raise RuntimeError("No timelapse session is running")
            self._status.running = False
            self._status.stopped_at = time.time()
            self._status.next_frame_at = None
            self._stop_event.set()
            thread = self._thread

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(5.0, self.status.interval_seconds + 2.0))
        with self._lock:
            self._thread = None
            self._write_metadata_locked()
            session_id = self._status.session_id

        should_render = self._render_on_stop if render is None else bool(render)
        if should_render and self.status.frame_count > 0:
            self.render(session_id)
        self.prune()
        log.info("Timelapse session %s stopped", session_id)
        return self.status

    def render(self, session_id: str, output_fps: Optional[int] = None) -> dict:
        """Render one session to H.264 MP4 using ffmpeg."""
        session_id = self._validate_session_id(session_id)
        session_dir = self._session_dir(session_id)
        metadata = self._read_metadata(session_id)
        frames_dir = session_dir / "frames"
        frames = sorted(frames_dir.glob("frame_*.jpg"))
        if not frames:
            raise RuntimeError("The timelapse session has no frames")
        fps = self._validate_output_fps(
            metadata.get("output_fps", self._default_output_fps)
            if output_fps is None
            else output_fps
        )
        video_path = session_dir / "timelapse.mp4"
        temporary = session_dir / "timelapse.mp4.tmp"

        with self._lock:
            if self._status.running and self._status.session_id == session_id:
                raise RuntimeError("Stop the active session before rendering it")
            if self._status.rendering:
                raise RuntimeError("A timelapse render is already active")
            self._status.rendering = True
            self._status.error = ""

        command = [
            self._ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(frames_dir / "frame_%06d.jpg"),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(temporary),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=1800,
            )
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg render failed: {result.stderr[-2000:]}")
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise RuntimeError("ffmpeg did not create a valid timelapse video")
            temporary.replace(video_path)
            metadata["output_fps"] = fps
            metadata["video_path"] = str(video_path)
            metadata["video_bytes"] = video_path.stat().st_size
            metadata["rendered_at"] = time.time()
            self._write_metadata(session_id, metadata)
            with self._lock:
                if self._status.session_id == session_id:
                    self._status.output_fps = fps
                    self._status.video_path = str(video_path)
                    self._write_metadata_locked()
            log.info("Rendered timelapse %s to %s", session_id, video_path)
            return self.session(session_id)
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            with self._lock:
                self._status.error = str(exc)
            raise
        finally:
            with self._lock:
                self._status.rendering = False

    def sessions(self) -> list[dict]:
        """List sessions newest-first, tolerating partially written metadata."""
        if not self._root_dir.exists():
            return []
        sessions = []
        for directory in self._root_dir.iterdir():
            if not directory.is_dir() or not _SESSION_RE.match(directory.name):
                continue
            try:
                sessions.append(self.session(directory.name))
            except (OSError, ValueError, json.JSONDecodeError):
                log.warning("Ignoring invalid timelapse session: %s", directory)
        return sorted(
            sessions,
            key=lambda item: float(item.get("started_at") or 0),
            reverse=True,
        )

    def session(self, session_id: str) -> dict:
        session_id = self._validate_session_id(session_id)
        session_dir = self._session_dir(session_id)
        if not session_dir.is_dir():
            raise FileNotFoundError(session_id)
        metadata = self._read_metadata(session_id)
        frames = sorted((session_dir / "frames").glob("frame_*.jpg"))
        video = session_dir / "timelapse.mp4"
        metadata.update(
            {
                "session_id": session_id,
                "frame_count": len(frames),
                "video_available": video.is_file(),
                "video_bytes": video.stat().st_size if video.is_file() else 0,
            }
        )
        return metadata

    def video_path(self, session_id: str) -> Path:
        session_id = self._validate_session_id(session_id)
        path = self._session_dir(session_id) / "timelapse.mp4"
        if not path.is_file():
            raise FileNotFoundError(session_id)
        return path

    def delete(self, session_id: str) -> None:
        session_id = self._validate_session_id(session_id)
        with self._lock:
            if self._status.running and self._status.session_id == session_id:
                raise RuntimeError("The active timelapse session cannot be deleted")
        directory = self._session_dir(session_id)
        if not directory.is_dir():
            raise FileNotFoundError(session_id)
        shutil.rmtree(directory)
        log.info("Deleted timelapse session %s", session_id)

    def update_defaults(
        self,
        interval_seconds: Optional[float] = None,
        output_fps: Optional[int] = None,
        max_sessions: Optional[int] = None,
        max_age_days: Optional[int] = None,
        render_on_stop: Optional[bool] = None,
    ) -> dict:
        with self._lock:
            if interval_seconds is not None:
                self._default_interval = self._validate_interval(interval_seconds)
            if output_fps is not None:
                self._default_output_fps = self._validate_output_fps(output_fps)
            if max_sessions is not None:
                self._max_sessions = max(0, int(max_sessions))
            if max_age_days is not None:
                self._max_age_days = max(0, int(max_age_days))
            if render_on_stop is not None:
                self._render_on_stop = bool(render_on_stop)
        self.prune()
        return self.defaults()

    def defaults(self) -> dict:
        return {
            "interval_seconds": self._default_interval,
            "output_fps": self._default_output_fps,
            "max_sessions": self._max_sessions,
            "max_age_days": self._max_age_days,
            "render_on_stop": self._render_on_stop,
            "root_dir": str(self._root_dir),
        }

    def prune(self, now: Optional[float] = None) -> list[str]:
        sessions = sorted(
            self.sessions(),
            key=lambda item: float(item.get("started_at") or 0),
        )
        current_time = time.time() if now is None else float(now)
        deleted = []

        if self._max_age_days > 0:
            cutoff = current_time - self._max_age_days * 86400
            for item in list(sessions):
                if float(item.get("started_at") or 0) >= cutoff:
                    continue
                if self._can_prune(str(item["session_id"])):
                    self.delete(str(item["session_id"]))
                    deleted.append(str(item["session_id"]))
                    sessions.remove(item)

        if self._max_sessions > 0:
            while len(sessions) > self._max_sessions:
                item = sessions.pop(0)
                session_id = str(item["session_id"])
                if not self._can_prune(session_id):
                    continue
                self.delete(session_id)
                deleted.append(session_id)
        return deleted

    def shutdown(self) -> None:
        """Stop capture without rendering during process shutdown."""
        if self.status.running:
            try:
                self.stop(render=False)
            except Exception as exc:
                log.warning("Failed to stop timelapse cleanly: %s", exc)

    def _capture_loop(self) -> None:
        next_capture = time.monotonic()
        while not self._stop_event.is_set():
            wait_seconds = max(0.0, next_capture - time.monotonic())
            if self._stop_event.wait(wait_seconds):
                break
            started = time.monotonic()
            try:
                frame = self._capture_fn()
                if not frame:
                    raise RuntimeError("Camera returned an empty timelapse frame")
                self._store_frame(frame)
            except Exception as exc:
                log.warning("Timelapse capture failed: %s", exc)
                with self._lock:
                    self._status.error = str(exc)
            next_capture = max(next_capture + self.status.interval_seconds, started + 0.01)
            with self._lock:
                if self._status.running:
                    self._status.next_frame_at = time.time() + max(
                        0.0,
                        next_capture - time.monotonic(),
                    )
                    self._write_metadata_locked()

    def _store_frame(self, frame: bytes) -> Path:
        with self._lock:
            if not self._status.running:
                raise RuntimeError("Timelapse capture stopped")
            sequence = self._status.frame_count + 1
            frames_dir = self._session_dir(self._status.session_id) / "frames"
            path = frames_dir / f"frame_{sequence:06d}.jpg"
            temporary = path.with_suffix(".jpg.tmp")
            temporary.write_bytes(frame)
            temporary.replace(path)
            captured_at = time.time()
            self._status.frame_count = sequence
            self._status.last_frame_at = captured_at
            self._status.error = ""
            self._write_metadata_locked()
            return path

    def _write_metadata_locked(self) -> None:
        if not self._status.session_id:
            return
        self._write_metadata(self._status.session_id, self._status.as_dict())

    def _write_metadata(self, session_id: str, metadata: dict) -> None:
        directory = self._session_dir(session_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "metadata.json"
        temporary = directory / "metadata.json.tmp"
        temporary.write_text(
            json.dumps(metadata, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _read_metadata(self, session_id: str) -> dict:
        path = self._session_dir(session_id) / "metadata.json"
        if not path.is_file():
            return {"session_id": session_id}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"session_id": session_id}

    def _session_dir(self, session_id: str) -> Path:
        return self._root_dir / self._validate_session_id(session_id)

    def _new_session_id(self) -> str:
        base = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        candidate = base
        suffix = 1
        while self._session_dir(candidate).exists():
            suffix += 1
            candidate = f"{base}_{suffix}"
        return candidate

    def _can_prune(self, session_id: str) -> bool:
        status = self.status
        return not (status.running and status.session_id == session_id)

    @staticmethod
    def _validate_session_id(session_id: str) -> str:
        value = str(session_id)
        if not _SESSION_RE.match(value):
            raise ValueError("Invalid timelapse session ID")
        return value

    @staticmethod
    def _validate_interval(value: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Timelapse interval must be a number") from exc
        if not 0.5 <= parsed <= 86400:
            raise ValueError("Timelapse interval must be between 0.5 and 86400 seconds")
        return parsed

    @staticmethod
    def _validate_output_fps(value: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Timelapse output FPS must be an integer") from exc
        if not 1 <= parsed <= 120:
            raise ValueError("Timelapse output FPS must be between 1 and 120")
        return parsed
