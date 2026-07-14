"""REST API blueprint — all JSON endpoints live under /api/v1."""

import logging
import socket
from copy import deepcopy

from flask import Blueprint, current_app, jsonify, request

from bambucam.main import _effective_mjpeg_fps
from bambucam.system_info import system_summary
from bambucam.web.security import hash_password

log = logging.getLogger(__name__)
api_bp = Blueprint("api", __name__)
_ALLOWED_SECTIONS = {"camera", "streaming", "web", "system"}


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


def _updater():
    return current_app.config["updater"]


def _json_object() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def _integer(value, name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _validate_config_update(data: dict) -> None:
    unknown = set(data) - _ALLOWED_SECTIONS
    if unknown:
        raise ValueError(f"Unknown config section(s): {', '.join(sorted(unknown))}")
    if any(not isinstance(value, dict) for value in data.values()):
        raise ValueError("Every config section must be an object")

    streaming = data.get("streaming", {})
    mjpeg = streaming.get("mjpeg", {})
    if mjpeg and not isinstance(mjpeg, dict):
        raise ValueError("streaming.mjpeg must be an object")
    if "port" in mjpeg:
        _integer(mjpeg["port"], "MJPEG/Web port", 1, 65535)
    if "quality" in mjpeg:
        _integer(mjpeg["quality"], "MJPEG quality", 1, 100)
    if "fps" in mjpeg:
        _integer(mjpeg["fps"], "MJPEG FPS", 1, 120)

    rtsp = streaming.get("rtsp", {})
    if rtsp and not isinstance(rtsp, dict):
        raise ValueError("streaming.rtsp must be an object")
    for key, label in (
        ("port", "RTSP port"),
        ("hls_port", "HLS port"),
        ("webrtc_port", "WebRTC port"),
    ):
        if key in rtsp:
            _integer(rtsp[key], label, 1, 65535)
    if "bitrate_kbps" in rtsp:
        _integer(rtsp["bitrate_kbps"], "RTSP bitrate", 100, 100000)
    if "stream_name" in rtsp:
        name = str(rtsp["stream_name"]).strip()
        if not name or any(char in name for char in " /?#"):
            raise ValueError("Invalid RTSP stream name")

    web = data.get("web", {})
    if "port" in web:
        _integer(web["port"], "Web port", 1, 65535)
    auth = web.get("auth", {})
    if auth and not isinstance(auth, dict):
        raise ValueError("web.auth must be an object")
    if auth.get("enabled") and not (
        auth.get("password")
        or auth.get("api_token")
        or _cfg().get("web", "auth", "password")
        or _cfg().get("web", "auth", "api_token")
    ):
        raise ValueError("Authentication requires a password or API token")


def _normalise_config_update(data: dict) -> dict:
    update = deepcopy(data)
    streaming = update.setdefault("streaming", {}) if "streaming" in update else {}
    mjpeg = streaming.setdefault("mjpeg", {}) if streaming else {}
    web = update.setdefault("web", {}) if "web" in update else {}

    # MJPEG is served by Flask and therefore shares the WebUI port. Keep the
    # legacy setting as an alias so existing installations and the current UI
    # remain compatible.
    if "port" in mjpeg:
        update.setdefault("web", {})["port"] = int(mjpeg["port"])
    elif "port" in web:
        update.setdefault("streaming", {}).setdefault("mjpeg", {})["port"] = int(web["port"])

    auth = update.get("web", {}).get("auth")
    if isinstance(auth, dict) and "password" in auth:
        if not auth["password"] or auth["password"] == "***":
            auth.pop("password")
        else:
            auth["password"] = hash_password(str(auth["password"]))
    return update


@api_bp.get("/camera/status")
def camera_status():
    return jsonify(_camera().status())


@api_bp.get("/camera/models")
def camera_models():
    from bambucam.camera.models import KNOWN_MODELS

    return jsonify(
        [
            {
                "id": model.id,
                "name": model.name,
                "sensor": model.sensor,
                "megapixels": model.megapixels,
                "max_resolution": str(model.max_resolution),
                "max_framerate": model.max_framerate,
                "supported_resolutions": [str(item) for item in model.supported_resolutions],
                "supported_framerates": model.supported_framerates,
                "resolution_max_framerates": {
                    str(resolution): fps
                    for resolution, fps in model.resolution_max_framerates.items()
                },
                "has_autofocus": model.has_autofocus,
                "has_hdr": model.has_hdr,
                "is_noir": model.is_noir,
                "has_global_shutter": model.has_global_shutter,
                "description": model.description,
            }
            for model in KNOWN_MODELS
        ]
    )


@api_bp.post("/camera/settings")
def camera_settings():
    try:
        data = _json_object()
        _camera().apply_settings(data)
        _cfg().update_section("camera", data)
        _cfg().save()

        if "resolution" in data or "framerate" in data:
            _rtsp().update_settings(
                resolution=str(_camera().current_resolution),
                framerate=_camera().current_framerate,
            )
            mjpeg_config = _cfg().get("streaming", "mjpeg", default={}) or {}
            _mjpeg().update_fps(_effective_mjpeg_fps(_camera().current_framerate, mjpeg_config))
    except Exception as exc:
        log.exception("Failed to apply camera settings")
        return jsonify({"error": str(exc)}), 400

    restart_keys = {"resolution", "framerate", "vflip", "hflip"}
    return jsonify(
        {
            "ok": True,
            "applied": data,
            "restarted": bool(restart_keys & data.keys()),
        }
    )


@api_bp.get("/camera/detect")
def camera_detect():
    from bambucam.camera.detector import detect_cameras

    return jsonify(
        [
            {
                "index": camera.index,
                "device": camera.device,
                "backend": camera.backend,
                "model": camera.model.name,
                "model_id": camera.model.id,
                "sensor": camera.model.sensor,
                "resolutions": [str(item) for item in camera.detected_resolutions],
            }
            for camera in detect_cameras()
        ]
    )


@api_bp.get("/stream/status")
def stream_status():
    host = _local_ip()
    web_port = _cfg().get("web", "port", default=8080)
    scheme = "https" if _cfg().get("web", "https", "enabled", default=False) else "http"
    mjpeg = _mjpeg()
    rtsp = _rtsp()
    return jsonify(
        {
            "mjpeg": {
                "running": mjpeg.is_running,
                "url": f"{scheme}://{host}:{web_port}/stream",
                "snapshot_url": f"{scheme}://{host}:{web_port}/snapshot",
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
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@api_bp.post("/stream/rtsp/stop")
def rtsp_stop():
    _rtsp().stop()
    return jsonify({"ok": True})


@api_bp.post("/stream/rtsp/settings")
def rtsp_settings():
    try:
        data = _json_object()
        _rtsp().update_settings(
            resolution=data.get("resolution"),
            framerate=data.get("framerate"),
            bitrate_kbps=data.get("bitrate_kbps"),
            stream_name=data.get("stream_name"),
            rtsp_port=data.get("port"),
            hls_port=data.get("hls_port"),
            webrtc_port=data.get("webrtc_port"),
            enable_hls=data.get("enable_hls"),
            enable_webrtc=data.get("enable_webrtc"),
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@api_bp.get("/snapshot")
def snapshot():
    save = request.args.get("save", "false").lower() in {"1", "true", "yes", "on"}
    try:
        frame = _snapshot().capture(save=save)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    from flask import Response

    return Response(frame, mimetype="image/jpeg")


@api_bp.get("/snapshot/list")
def snapshot_list():
    return jsonify(_snapshot().list_snapshots())


@api_bp.delete("/snapshot/<path:filename>")
def snapshot_delete(filename: str):
    try:
        _snapshot().delete_snapshot(filename)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError:
        return jsonify({"error": "Snapshot not found"}), 404
    return jsonify({"ok": True})


@api_bp.get("/config")
def get_config():
    config = _cfg().as_dict()
    _redact(config, ["web", "auth", "password"])
    _redact(config, ["web", "auth", "api_token"])
    _redact(config, ["streaming", "rtsp", "auth", "password"])
    return jsonify(config)


@api_bp.post("/config")
def set_config():
    try:
        data = _normalise_config_update(_json_object())
        _validate_config_update(data)
        for section, values in data.items():
            _cfg().update_section(section, values)
        _cfg().save()
    except Exception as exc:
        log.exception("Failed to update config")
        return jsonify({"error": str(exc)}), 400

    streaming = data.get("streaming", {})
    mjpeg_data = streaming.get("mjpeg", {})
    if "quality" in mjpeg_data:
        _camera().set_jpeg_quality(int(mjpeg_data["quality"]))
    if "fps" in mjpeg_data:
        _mjpeg().update_fps(_effective_mjpeg_fps(_camera().current_framerate, mjpeg_data))

    rtsp_data = streaming.get("rtsp", {})
    runtime_rtsp_keys = {
        "bitrate_kbps",
        "stream_name",
        "port",
        "hls_port",
        "webrtc_port",
        "enable_hls",
        "enable_webrtc",
    }
    if runtime_rtsp_keys & rtsp_data.keys():
        try:
            _rtsp().update_settings(
                bitrate_kbps=rtsp_data.get("bitrate_kbps"),
                stream_name=rtsp_data.get("stream_name"),
                rtsp_port=rtsp_data.get("port"),
                hls_port=rtsp_data.get("hls_port"),
                webrtc_port=rtsp_data.get("webrtc_port"),
                enable_hls=rtsp_data.get("enable_hls"),
                enable_webrtc=rtsp_data.get("enable_webrtc"),
            )
        except Exception as exc:
            log.warning("Saved RTSP settings but failed to apply them: %s", exc)
            return jsonify({"error": f"Settings saved but runtime apply failed: {exc}"}), 500

    web_data = data.get("web", {})
    restart_required = bool({"port", "host", "auth", "https", "trust_proxy"} & web_data.keys())
    return jsonify({"ok": True, "restart_required": restart_required})


@api_bp.get("/system")
def system():
    return jsonify(system_summary())


@api_bp.post("/system/restart-camera")
def restart_camera():
    try:
        _camera().restart()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True})


@api_bp.get("/update/status")
def update_status():
    return jsonify(_updater().status.as_dict())


@api_bp.post("/update/check")
def update_check():
    return jsonify(_updater().check().as_dict())


@api_bp.post("/update/start")
def update_start():
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        target_version = data.get("version")
        started = _updater().start_update(target_version=target_version)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    if not started:
        return (
            jsonify(
                {
                    "error": "Update not started — version unavailable or update already active.",
                    "status": _updater().status.as_dict(),
                }
            ),
            409,
        )
    message = f"Installation of v{target_version} started." if target_version else "Update started."
    return jsonify({"ok": True, "message": message})


@api_bp.get("/update/releases")
def update_releases():
    return jsonify(_updater().list_releases())


def _local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _redact(data: dict, path: list[str]) -> None:
    node = data
    for key in path[:-1]:
        if not isinstance(node.get(key), dict):
            return
        node = node[key]
    if path[-1] in node and node[path[-1]]:
        node[path[-1]] = "***"
