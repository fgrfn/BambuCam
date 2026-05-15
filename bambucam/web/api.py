"""
REST API blueprint — /api/v1/...

All responses are JSON. Errors return {"error": "message"} with
appropriate HTTP status codes.
"""

import logging
import socket

from flask import Blueprint, current_app, jsonify, request

from bambucam.system_info import system_summary

log = logging.getLogger(__name__)
api_bp = Blueprint("api", __name__)


def _camera():
    return current_app.config["camera_manager"]


def _mjpeg():
    return current_app.config["mjpeg_streamer"]


def _rtsp():
    return current_app.config["rtsp_streamer"]


def _snapshot():
    return current_app.config["snapshot_service"]


def _cfg():
    return current_app.config["bambucam_config"]


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------


@api_bp.get("/camera/status")
def camera_status():
    return jsonify(_camera().status())


@api_bp.get("/camera/models")
def camera_models():
    from bambucam.camera.models import KNOWN_MODELS

    return jsonify(
        [
            {
                "id": m.id,
                "name": m.name,
                "sensor": m.sensor,
                "megapixels": m.megapixels,
                "max_resolution": str(m.max_resolution),
                "max_framerate": m.max_framerate,
                "supported_resolutions": [str(r) for r in m.supported_resolutions],
                "supported_framerates": m.supported_framerates,
                "resolution_max_framerates": {
                    str(r): fps for r, fps in m.resolution_max_framerates.items()
                },
                "has_autofocus": m.has_autofocus,
                "has_hdr": m.has_hdr,
                "is_noir": m.is_noir,
                "has_global_shutter": m.has_global_shutter,
                "description": m.description,
            }
            for m in KNOWN_MODELS
        ]
    )


@api_bp.post("/camera/settings")
def camera_settings():
    data = request.get_json(silent=True) or {}
    try:
        _camera().apply_settings(data)
        _cfg().update_section("camera", data)
        _cfg().save()
        # Keep RTSPStreamer and MJPEGStreamer in sync when resolution or framerate change
        if "resolution" in data or "framerate" in data:
            _rtsp().update_settings(
                resolution=data.get("resolution"),
                framerate=data.get("framerate"),
            )
        if "framerate" in data:
            _mjpeg().update_fps(int(data["framerate"]))
    except Exception as e:
        log.exception("Failed to apply camera settings")
        return jsonify({"error": str(e)}), 400
    restart_keys = {"resolution", "framerate", "vflip", "hflip"}
    restarted = bool(restart_keys & data.keys())
    return jsonify({"ok": True, "applied": data, "restarted": restarted})


@api_bp.get("/camera/detect")
def camera_detect():
    from bambucam.camera.detector import detect_cameras

    cameras = detect_cameras()
    return jsonify(
        [
            {
                "index": c.index,
                "device": c.device,
                "backend": c.backend,
                "model": c.model.name,
                "model_id": c.model.id,
                "sensor": c.model.sensor,
                "resolutions": [str(r) for r in c.detected_resolutions],
            }
            for c in cameras
        ]
    )


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


@api_bp.get("/stream/status")
def stream_status():
    host = _local_ip()
    cfg = _cfg()
    mjpeg_port = cfg.get("streaming", "mjpeg", "port")
    rtsp = _rtsp()
    mjpeg = _mjpeg()

    return jsonify(
        {
            "mjpeg": {
                "running": mjpeg.is_running,
                "url": f"http://{host}:{mjpeg_port}/stream",
                "snapshot_url": f"http://{host}:{mjpeg_port}/snapshot",
                "clients": mjpeg.client_count,
                "actual_fps": mjpeg.actual_fps,
            },
            "rtsp": rtsp.status() | {"urls": rtsp.stream_urls(host)},
        }
    )


@api_bp.post("/stream/rtsp/start")
def rtsp_start():
    try:
        _rtsp().start()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


@api_bp.post("/stream/rtsp/stop")
def rtsp_stop():
    _rtsp().stop()
    return jsonify({"ok": True})


@api_bp.post("/stream/rtsp/settings")
def rtsp_settings():
    data = request.get_json(silent=True) or {}
    _rtsp().update_settings(
        resolution=data.get("resolution"),
        framerate=data.get("framerate"),
        bitrate_kbps=data.get("bitrate_kbps"),
    )
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


@api_bp.get("/snapshot")
def snapshot():
    save = request.args.get("save", "false").lower() == "true"
    try:
        frame = _snapshot().capture(save=save)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    from flask import Response

    return Response(frame, mimetype="image/jpeg")


@api_bp.get("/snapshot/list")
def snapshot_list():
    return jsonify(_snapshot().list_snapshots())


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@api_bp.get("/config")
def get_config():
    cfg = _cfg().as_dict()
    # Redact passwords before sending to browser
    _redact(cfg, ["web", "auth", "password"])
    _redact(cfg, ["streaming", "rtsp", "auth", "password"])
    return jsonify(cfg)


@api_bp.post("/config")
def set_config():
    data = request.get_json(silent=True) or {}
    for section, values in data.items():
        if isinstance(values, dict):
            _cfg().update_section(section, values)
    try:
        _cfg().save()
    except Exception as e:
        log.exception("Failed to persist config")
        return jsonify({"error": f"Config updated in memory but not saved: {e}"}), 500
    # Apply MJPEG streaming changes to running services immediately
    streaming_data = data.get("streaming", {})
    mjpeg_data = streaming_data.get("mjpeg", {})
    if "fps" in mjpeg_data:
        try:
            _mjpeg().update_fps(int(mjpeg_data["fps"]))
        except Exception as e:
            log.warning("Failed to update MJPEG fps: %s", e)
    if "quality" in mjpeg_data:
        try:
            _camera().set_jpeg_quality(int(mjpeg_data["quality"]))
        except Exception as e:
            log.warning("Failed to update MJPEG quality: %s", e)
    rtsp_data = streaming_data.get("rtsp", {})
    if any(k in rtsp_data for k in ("resolution", "framerate", "bitrate_kbps")):
        try:
            _rtsp().update_settings(
                resolution=rtsp_data.get("resolution"),
                framerate=rtsp_data.get("framerate"),
                bitrate_kbps=rtsp_data.get("bitrate_kbps"),
            )
        except Exception as e:
            log.warning("Failed to update RTSP settings: %s", e)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


@api_bp.get("/system")
def system():
    return jsonify(system_summary())


@api_bp.post("/system/restart-camera")
def restart_camera():
    try:
        _camera().restart()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def _updater():
    return current_app.config["updater"]


@api_bp.get("/update/status")
def update_status():
    return jsonify(_updater().status.as_dict())


@api_bp.post("/update/check")
def update_check():
    status = _updater().check()
    return jsonify(status.as_dict())


@api_bp.post("/update/start")
def update_start():
    data = request.get_json(silent=True) or {}
    target_version = data.get("version")  # optional — None means "latest"
    started = _updater().start_update(target_version=target_version)
    if not started:
        status = _updater().status
        return (
            jsonify(
                {
                    "error": "Update nicht gestartet — Version nicht gefunden oder bereits aktiv.",
                    "status": status.as_dict(),
                }
            ),
            409,
        )
    msg = (
        f"Installation von v{target_version} gestartet." if target_version else "Update gestartet."
    )
    return jsonify({"ok": True, "message": msg})


@api_bp.get("/update/releases")
def update_releases():
    return jsonify(_updater().list_releases())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _redact(d: dict, path: list) -> None:
    node = d
    for k in path[:-1]:
        if not isinstance(node.get(k), dict):
            return
        node = node[k]
    if path[-1] in node:
        node[path[-1]] = "***"
