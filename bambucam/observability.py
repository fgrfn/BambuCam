"""Health, diagnostics, logging, and Prometheus helpers for BambuCam."""

import io
import json
import logging
import math
import time
import zipfile
from collections import deque
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Optional

from bambucam import __version__
from bambucam.system_info import system_summary

_SECRET_KEYS = {
    "password",
    "pass",
    "api_token",
    "token",
    "secret",
    "secret_key",
    "key",
    "cert",
}


class RingBufferLogHandler(logging.Handler):
    """Keep a bounded, thread-safe copy of recent formatted log messages."""

    def __init__(self, capacity: int = 300):
        super().__init__(level=logging.INFO)
        self._records: deque = deque(maxlen=max(20, int(capacity)))
        self._lock = Lock()
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return
        with self._lock:
            self._records.append(message)

    def lines(self, limit: Optional[int] = None) -> list[str]:
        with self._lock:
            values = list(self._records)
        if limit is not None:
            values = values[-max(0, int(limit)) :]
        return values


_log_handler: Optional[RingBufferLogHandler] = None
_handler_lock = Lock()


def install_log_buffer(capacity: int = 300) -> RingBufferLogHandler:
    """Install one global in-memory log handler and return it."""
    global _log_handler
    with _handler_lock:
        if _log_handler is None:
            _log_handler = RingBufferLogHandler(capacity=capacity)
            logging.getLogger().addHandler(_log_handler)
        return _log_handler


def recent_logs(limit: int = 300) -> list[str]:
    handler = install_log_buffer(capacity=max(300, limit))
    return handler.lines(limit=limit)


def redact(value: Any, key: str = "") -> Any:
    """Recursively redact credentials and local certificate/key paths."""
    if key.lower() in _SECRET_KEYS and value not in (None, "", False):
        return "***"
    if isinstance(value, dict):
        return {
            str(item_key): redact(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    return value


def health_payload(camera, mjpeg, rtsp) -> tuple[dict, int]:
    """Return liveness/readiness information and an HTTP status code."""
    camera_running = bool(camera.is_running)
    mjpeg_running = bool(mjpeg.is_running)
    rtsp_status = rtsp.status()
    rtsp_requested = bool(rtsp_status.get("running"))
    rtsp_healthy = not rtsp_requested or (
        bool(rtsp_status.get("mediamtx_running"))
        and bool(rtsp_status.get("publisher_running"))
    )

    ready = camera_running and (mjpeg_running or rtsp_healthy)
    status = "ok" if ready else "degraded"
    payload = {
        "status": status,
        "ready": ready,
        "version": __version__,
        "camera_running": camera_running,
        "mjpeg_running": mjpeg_running,
        "rtsp_running": rtsp_requested,
        "rtsp_healthy": rtsp_healthy,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return payload, 200 if ready else 503


def diagnostics_payload(config, camera, mjpeg, rtsp, snapshot, updater) -> dict:
    """Build a support payload without exposing credentials."""
    snapshots = snapshot.list_snapshots()
    snapshot_bytes = sum(int(item.get("size", 0)) for item in snapshots)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": __version__,
        "system": system_summary(),
        "camera": camera.status(),
        "mjpeg": {
            "running": mjpeg.is_running,
            "clients": mjpeg.client_count,
            "actual_fps": mjpeg.actual_fps,
        },
        "rtsp": rtsp.status(),
        "snapshots": {
            "count": len(snapshots),
            "bytes": snapshot_bytes,
            "directory": str(snapshot.snapshot_dir),
        },
        "updater": updater.status.as_dict(),
        "config": redact(config.as_dict()),
        "logs": recent_logs(
            limit=int(config.get("system", "diagnostics_log_lines", default=300))
        ),
    }


def diagnostics_zip(payload: dict) -> bytes:
    """Encode diagnostics as a small ZIP with JSON and plaintext logs."""
    memory = io.BytesIO()
    logs = payload.get("logs", [])
    json_payload = dict(payload)
    json_payload["logs"] = ["See logs.txt"] if logs else []
    with zipfile.ZipFile(memory, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "diagnostics.json",
            json.dumps(json_payload, indent=2, sort_keys=True, ensure_ascii=False),
        )
        archive.writestr(
            "logs.txt",
            "\n".join(str(line) for line in logs) + ("\n" if logs else ""),
        )
    return memory.getvalue()


def prometheus_payload(config, camera, mjpeg, rtsp, snapshot, updater) -> str:
    """Render dependency-free Prometheus exposition text."""
    system = system_summary()
    camera_status = camera.status()
    rtsp_status = rtsp.status()
    updater_status = updater.status.as_dict()
    snapshots = snapshot.list_snapshots()

    lines = [
        "# HELP bambucam_info Static BambuCam build information.",
        "# TYPE bambucam_info gauge",
        f'bambucam_info{{version="{_escape_label(__version__)}"}} 1',
    ]

    _metric(
        lines,
        "bambucam_camera_running",
        camera_status.get("running"),
        "Camera running state",
    )
    _metric(lines, "bambucam_mjpeg_running", mjpeg.is_running, "MJPEG capture loop state")
    _metric(lines, "bambucam_mjpeg_clients", mjpeg.client_count, "Connected MJPEG clients")
    _metric(lines, "bambucam_mjpeg_fps", mjpeg.actual_fps, "Measured MJPEG frames per second")
    _metric(lines, "bambucam_rtsp_running", rtsp_status.get("running"), "RTSP service state")
    _metric(
        lines,
        "bambucam_rtsp_publisher_running",
        rtsp_status.get("publisher_running"),
        "RTSP publisher state",
    )
    _metric(
        lines,
        "bambucam_cpu_temperature_celsius",
        system.get("cpu_temp_c"),
        "CPU temperature",
    )
    _metric(
        lines,
        "bambucam_cpu_usage_percent",
        system.get("cpu_usage_pct"),
        "CPU utilization",
    )
    _metric(
        lines,
        "bambucam_memory_available_bytes",
        _mb_to_bytes(system.get("memory", {}).get("available_mb")),
        "Available memory",
    )
    _metric(
        lines,
        "bambucam_disk_free_bytes",
        _gb_to_bytes(system.get("disk", {}).get("free_gb")),
        "Free filesystem space",
    )
    _metric(lines, "bambucam_uptime_seconds", system.get("uptime_seconds"), "System uptime")
    _metric(lines, "bambucam_snapshots_total", len(snapshots), "Saved snapshot files")
    _metric(
        lines,
        "bambucam_snapshot_bytes",
        sum(int(item.get("size", 0)) for item in snapshots),
        "Bytes used by saved snapshots",
    )
    _metric(
        lines,
        "bambucam_update_available",
        updater_status.get("update_available"),
        "Whether a software update is available",
    )
    _metric(
        lines,
        "bambucam_authentication_enabled",
        config.get("web", "auth", "enabled", default=False),
        "Whether WebUI authentication is enabled",
    )
    return "\n".join(lines) + "\n"


def _metric(lines: list[str], name: str, value: Any, help_text: str) -> None:
    lines.extend([f"# HELP {name} {help_text}.", f"# TYPE {name} gauge"])
    if value is None:
        lines.append(f"{name} NaN")
        return
    numeric = float(int(value)) if isinstance(value, bool) else float(value)
    if math.isnan(numeric) or math.isinf(numeric):
        rendered = "NaN"
    elif numeric.is_integer():
        rendered = str(int(numeric))
    else:
        rendered = format(numeric, ".6g")
    lines.append(f"{name} {rendered}")


def _mb_to_bytes(value: Any) -> Optional[float]:
    return None if value is None else float(value) * 1024 * 1024


def _gb_to_bytes(value: Any) -> Optional[float]:
    return None if value is None else float(value) * 1024 * 1024 * 1024


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
