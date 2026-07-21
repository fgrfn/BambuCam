"""REST API blueprint — all JSON endpoints live under /api/v1."""

import logging
import socket
from copy import deepcopy

from flask import Blueprint, current_app, jsonify, request

from bambucam.config import validate_config_update
from bambucam.main import _effective_mjpeg_fps
from bambucam.system_control import schedule_system_reboot
from bambucam.system_info import hardware_recommendations, system_summary
from bambucam.web.security import hash_password

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


def _updater():
    return current_app.config["updater"]


def _json_object() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def _validate_config_update(data: dict) -> None:
    validate_config_update(data, _cfg().as_dict())


def _restore_config(snapshot: dict) -> None:
    """Restore only in-memory config; atomic saves leave the old file intact on failure."""
    config = _cfg()
    if hasattr(config, "replace"):
        config.replace(snapshot)
    elif hasattr(config, "data"):  # Lightweight test/config adapters.
        config.data = deepcopy(snapshot)


def _rtsp_runtime_settings(config: dict) -> dict:
    rtsp = config["streaming"]["rtsp"]
    return {
        "bitrate_kbps": rtsp["bitrate_kbps"],
        "stream_name": rtsp["stream_name"],
        "rtsp_port": rtsp["port"],
        "hls_port": rtsp["hls_port"],
        "webrtc_port": rtsp["webrtc_port"],
        "enable_hls": rtsp["enable_hls"],
        "enable_webrtc": rtsp["enable_webrtc"],
    }


def _rollback_runtime(
    config: dict,
    *,
    camera=False,
    mjpeg=False,
    rtsp=False,
    camera_status=None,
    mjpeg_running=None,
    rtsp_running=None,
) -> None:
    """Best-effort runtime rollback used after a failed transactional update."""
    try:
        if camera:
            status = camera_status or _camera().status()
            old_camera = config["camera"]
            settings = {
                key: old_camera[key]
                for key in (
                    "brightness",
                    "contrast",
                    "saturation",
                    "sharpness",
                    "zoom",
                    "exposure_mode",
                    "awb_mode",
                    "noise_reduction",
                    "vflip",
                    "hflip",
                    "autofocus",
                    "hdr",
                )
            }
            if status.get("resolution"):
                settings["resolution"] = status["resolution"]
            if status.get("framerate"):
                settings["framerate"] = status["framerate"]
            _camera().apply_settings(settings)
        if mjpeg:
            old_mjpeg = config["streaming"]["mjpeg"]
            _camera().set_jpeg_quality(int(old_mjpeg["quality"]))
            _mjpeg().update_fps(int(old_mjpeg["fps"]))
            if mjpeg_running is True and not _mjpeg().is_running:
                _mjpeg().start()
            elif mjpeg_running is False and _mjpeg().is_running:
                _mjpeg().stop()
        if rtsp:
            _rtsp().update_settings(**_rtsp_runtime_settings(config))
            if rtsp_running is True and not _rtsp().is_running:
                _rtsp().start()
            elif rtsp_running is False and _rtsp().is_running:
                _rtsp().stop()
    except Exception:
        log.exception("Runtime rollback failed")


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


def _restart_reasons(data: dict) -> list[str]:
    reasons = []
    web = data.get("web", {})
    if {"port", "host", "auth", "https", "trust_proxy"} & web.keys():
        reasons.append("web")
    if data.get("camera"):
        reasons.append("camera")
    if data.get("system"):
        reasons.append("system")
    streaming = data.get("streaming", {})
    mjpeg = streaming.get("mjpeg", {})
    if "path" in mjpeg:
        reasons.append("mjpeg")
    rtsp = streaming.get("rtsp", {})
    if "auth" in rtsp:
        reasons.append("rtsp_auth")
    if streaming.get("snapshot"):
        reasons.append("snapshot")
    if streaming.get("timelapse"):
        reasons.append("timelapse")
    return sorted(set(reasons))


def _overrides_camera_profile(data: dict) -> bool:
    """Return whether a config update changes values owned by camera profiles."""
    camera_keys = set(data.get("camera", {})) - {"active_profile"}
    streaming = data.get("streaming", {})
    mjpeg_keys = set(streaming.get("mjpeg", {})) & {"quality", "fps"}
    rtsp_keys = set(streaming.get("rtsp", {})) & {"bitrate_kbps"}
    return bool(camera_keys or mjpeg_keys or rtsp_keys)


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
    snapshot = _cfg().as_dict()
    old_camera_status = _camera().status()
    try:
        data = _json_object()
        _validate_config_update({"camera": data})
        _camera().apply_settings(data)

        if "resolution" in data or "framerate" in data:
            _rtsp().update_settings(
                resolution=str(_camera().current_resolution),
                framerate=_camera().current_framerate,
            )
            mjpeg_config = _cfg().get("streaming", "mjpeg", default={}) or {}
            _mjpeg().update_fps(_effective_mjpeg_fps(_camera().current_framerate, mjpeg_config))
        _cfg().update_section("camera", {**data, "active_profile": "custom"})
        _cfg().save()
    except Exception as exc:
        log.exception("Failed to apply camera settings")
        _restore_config(snapshot)
        _rollback_runtime(
            snapshot,
            camera=True,
            mjpeg=True,
            rtsp=True,
            camera_status=old_camera_status,
        )
        return jsonify({"error": str(exc)}), 400

    restart_keys = {"resolution", "framerate", "vflip", "hflip"}
    return jsonify(
        {
            "ok": True,
            "applied": data,
            "restarted": bool(restart_keys & data.keys()),
        }
    )


@api_bp.post("/stream/settings")
def stream_settings():
    """Apply camera mode and stream encoding values as one transaction."""
    snapshot = _cfg().as_dict()
    old_camera_status = _camera().status()
    old_mjpeg_running = bool(_mjpeg().is_running)
    old_rtsp_running = bool(_rtsp().is_running)
    camera_applied = False
    mjpeg_applied = False
    rtsp_applied = False
    try:
        data = _json_object()
        allowed = {"resolution", "framerate", "bitrate_kbps"}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"Unknown stream setting(s): {', '.join(sorted(unknown))}")
        if not data:
            raise ValueError("At least one stream setting is required")

        camera_data = {key: data[key] for key in ("resolution", "framerate") if key in data}
        config_update: dict = {}
        if camera_data:
            config_update["camera"] = camera_data
        streaming_update: dict = {}
        if "framerate" in data:
            streaming_update["mjpeg"] = {"fps": data["framerate"]}
        if "bitrate_kbps" in data:
            streaming_update["rtsp"] = {"bitrate_kbps": data["bitrate_kbps"]}
        if streaming_update:
            config_update["streaming"] = streaming_update
        _validate_config_update(config_update)

        if camera_data:
            camera_applied = True
            _camera().apply_settings(camera_data)

        resolution_value = (
            data.get("resolution")
            or _camera().current_resolution
            or old_camera_status.get("resolution")
        )
        framerate_value = (
            data.get("framerate")
            or _camera().current_framerate
            or old_camera_status.get("framerate")
        )
        resolution = str(resolution_value) if resolution_value is not None else None
        framerate = int(framerate_value) if framerate_value is not None else None
        if "framerate" in data:
            mjpeg_applied = True
            mjpeg_config = snapshot["streaming"]["mjpeg"] | {"fps": int(framerate)}
            _mjpeg().update_fps(_effective_mjpeg_fps(int(framerate), mjpeg_config))

        rtsp_applied = True
        _rtsp().update_settings(
            resolution=resolution,
            framerate=framerate,
            bitrate_kbps=data.get("bitrate_kbps"),
        )

        if camera_data:
            _cfg().update_section("camera", {**camera_data, "active_profile": "custom"})
        else:
            _cfg().update_section("camera", {"active_profile": "custom"})
        if streaming_update:
            _cfg().update_section("streaming", streaming_update)
        _cfg().save()
    except Exception as exc:
        log.exception("Failed to apply stream settings")
        _restore_config(snapshot)
        if camera_applied or mjpeg_applied or rtsp_applied:
            _rollback_runtime(
                snapshot,
                camera=camera_applied,
                mjpeg=mjpeg_applied,
                rtsp=rtsp_applied,
                camera_status=old_camera_status,
                mjpeg_running=old_mjpeg_running,
                rtsp_running=old_rtsp_running,
            )
        return jsonify({"error": str(exc)}), 400

    restart_keys = {"resolution", "framerate"}
    return jsonify(
        {
            "ok": True,
            "applied": data,
            "restarted": bool(restart_keys & data.keys()),
            "active_profile": "custom",
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
    snapshot = _cfg().as_dict()
    was_running = bool(_rtsp().is_running)
    try:
        _rtsp().start()
        _cfg().update_section("streaming", {"rtsp": {"enabled": True}})
        _cfg().save()
    except Exception as exc:
        _restore_config(snapshot)
        if not was_running:
            _rtsp().stop()
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "persisted": True})


@api_bp.post("/stream/rtsp/stop")
def rtsp_stop():
    snapshot = _cfg().as_dict()
    was_running = bool(_rtsp().is_running)
    try:
        _rtsp().stop()
        _cfg().update_section("streaming", {"rtsp": {"enabled": False}})
        _cfg().save()
    except Exception as exc:
        _restore_config(snapshot)
        if was_running:
            try:
                _rtsp().start()
            except Exception:
                log.exception("Failed to restore RTSP after persistence failure")
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "persisted": True})


@api_bp.post("/stream/rtsp/settings")
def rtsp_settings():
    snapshot = _cfg().as_dict()
    runtime_applied = False
    try:
        data = _json_object()
        persistent_keys = {
            "bitrate_kbps",
            "stream_name",
            "port",
            "hls_port",
            "webrtc_port",
            "enable_hls",
            "enable_webrtc",
        }
        persistent_settings = {key: value for key, value in data.items() if key in persistent_keys}
        if persistent_settings:
            _validate_config_update({"streaming": {"rtsp": persistent_settings}})

        runtime_applied = True
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
        if persistent_settings:
            _cfg().update_section("streaming", {"rtsp": persistent_settings})
            if "bitrate_kbps" in persistent_settings:
                _cfg().update_section("camera", {"active_profile": "custom"})
            _cfg().save()
    except Exception as exc:
        _restore_config(snapshot)
        if runtime_applied:
            _rollback_runtime(snapshot, rtsp=True)
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "persisted": sorted(persistent_settings)})


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
    snapshot = _cfg().as_dict()
    old_mjpeg_running = bool(_mjpeg().is_running)
    old_rtsp_running = bool(_rtsp().is_running)
    mjpeg_applied = False
    rtsp_applied = False
    try:
        data = _normalise_config_update(_json_object())
        profile_overridden = _overrides_camera_profile(data)
        _validate_config_update(data)
        streaming = data.get("streaming", {})
        mjpeg_data = streaming.get("mjpeg", {})
        if "quality" in mjpeg_data:
            mjpeg_applied = True
            _camera().set_jpeg_quality(int(mjpeg_data["quality"]))
        if "fps" in mjpeg_data:
            mjpeg_applied = True
            _mjpeg().update_fps(_effective_mjpeg_fps(_camera().current_framerate, mjpeg_data))
        if "enabled" in mjpeg_data:
            mjpeg_applied = True
            if mjpeg_data["enabled"]:
                _mjpeg().start()
            else:
                _mjpeg().stop()

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
            rtsp_applied = True
            _rtsp().update_settings(
                bitrate_kbps=rtsp_data.get("bitrate_kbps"),
                stream_name=rtsp_data.get("stream_name"),
                rtsp_port=rtsp_data.get("port"),
                hls_port=rtsp_data.get("hls_port"),
                webrtc_port=rtsp_data.get("webrtc_port"),
                enable_hls=rtsp_data.get("enable_hls"),
                enable_webrtc=rtsp_data.get("enable_webrtc"),
            )
        if "enabled" in rtsp_data:
            rtsp_applied = True
            enabled = rtsp_data["enabled"]
            if enabled == "auto":
                enabled = hardware_recommendations()["rtsp_enabled"]
            if enabled is True:
                _rtsp().start()
            elif enabled is False:
                _rtsp().stop()
        for section, values in data.items():
            _cfg().update_section(section, values)
        if profile_overridden:
            _cfg().update_section("camera", {"active_profile": "custom"})
        _cfg().save()
    except Exception as exc:
        log.exception("Failed to update config")
        _restore_config(snapshot)
        if mjpeg_applied or rtsp_applied:
            _rollback_runtime(
                snapshot,
                mjpeg=mjpeg_applied,
                rtsp=rtsp_applied,
                mjpeg_running=old_mjpeg_running,
                rtsp_running=old_rtsp_running,
            )
        return jsonify({"error": str(exc)}), 400

    restart_reasons = _restart_reasons(data)
    return jsonify(
        {
            "ok": True,
            "restart_required": bool(restart_reasons),
            "restart_reasons": restart_reasons,
        }
    )


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


@api_bp.post("/system/restart")
def restart_application():
    if not _updater().restart_service():
        return jsonify({"error": "A software update is currently active"}), 409
    return jsonify({"ok": True, "message": "BambuCam restart scheduled"}), 202


@api_bp.post("/system/reboot")
def reboot_system():
    if request.headers.get("X-BambuCam-CSRF") != "1":
        return jsonify({"error": "CSRF validation failed"}), 403
    try:
        data = _json_object()
        if data.get("confirm") != "reboot":
            raise ValueError("Explicit reboot confirmation is required")
        if not schedule_system_reboot():
            return jsonify({"error": "A system reboot is already pending"}), 409
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    return jsonify({"ok": True, "message": "Raspberry Pi reboot scheduled"}), 202


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
