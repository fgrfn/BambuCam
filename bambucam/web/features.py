"""Camera profile and timelapse HTTP API routes."""

import logging

from flask import Blueprint, current_app, jsonify, request, send_file

log = logging.getLogger(__name__)
features_bp = Blueprint("features", __name__)


def _profiles():
    return current_app.config["camera_profile_service"]


def _timelapse():
    return current_app.config["timelapse_service"]


def _config():
    return current_app.config["bambucam_config"]


def _json_object() -> dict:
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


@features_bp.get("/camera/profiles")
def camera_profiles():
    try:
        from bambucam.system_info import hardware_recommendations

        return jsonify(
            {
                "active": _config().get("camera", "active_profile", default="custom"),
                "recommended": hardware_recommendations()["recommended_profile"],
                "profiles": _profiles().list_profiles(),
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503


@features_bp.post("/camera/profiles/<name>")
def apply_camera_profile(name: str):
    try:
        result = _profiles().apply(name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        log.exception("Failed to apply camera profile %s", name)
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "profile": result})


@features_bp.get("/timelapse/status")
def timelapse_status():
    return jsonify(
        {
            "status": _timelapse().status.as_dict(),
            "defaults": _timelapse().defaults(),
        }
    )


@features_bp.get("/timelapse/sessions")
def timelapse_sessions():
    return jsonify(_timelapse().sessions())


@features_bp.post("/timelapse/start")
def timelapse_start():
    try:
        data = _json_object()
        status = _timelapse().start(
            title=data.get("title", ""),
            interval_seconds=data.get("interval_seconds"),
            output_fps=data.get("output_fps"),
        )
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "status": status.as_dict()})


@features_bp.post("/timelapse/stop")
def timelapse_stop():
    try:
        data = _json_object()
        status = _timelapse().stop(render=data.get("render"))
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        log.exception("Failed to stop timelapse")
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "status": status.as_dict()})


@features_bp.post("/timelapse/<session_id>/render")
def timelapse_render(session_id: str):
    try:
        data = _json_object()
        session = _timelapse().render(session_id, output_fps=data.get("output_fps"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError:
        return jsonify({"error": "Timelapse session not found"}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        log.exception("Failed to render timelapse %s", session_id)
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "session": session})


@features_bp.get("/timelapse/<session_id>/video")
def timelapse_video(session_id: str):
    try:
        path = _timelapse().video_path(session_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError:
        return jsonify({"error": "Timelapse video not found"}), 404
    return send_file(
        path,
        mimetype="video/mp4",
        as_attachment=True,
        download_name=f"bambucam-{session_id}.mp4",
        conditional=True,
    )


@features_bp.delete("/timelapse/<session_id>")
def timelapse_delete(session_id: str):
    try:
        _timelapse().delete(session_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError:
        return jsonify({"error": "Timelapse session not found"}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"ok": True})


@features_bp.post("/timelapse/settings")
def timelapse_settings():
    snapshot = _config().as_dict()
    runtime_applied = False
    try:
        data = _json_object()
        allowed = {
            "interval_seconds",
            "output_fps",
            "max_sessions",
            "max_age_days",
            "render_on_stop",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"Unknown timelapse setting(s): {', '.join(sorted(unknown))}")
        defaults = _timelapse().update_defaults(**data)
        runtime_applied = True
        _config().update_section("streaming", {"timelapse": defaults})
        _config().save()
    except Exception as exc:
        _config().replace(snapshot)
        if runtime_applied:
            try:
                old = snapshot["streaming"]["timelapse"]
                _timelapse().update_defaults(
                    **{
                        key: old[key]
                        for key in (
                            "interval_seconds",
                            "output_fps",
                            "max_sessions",
                            "max_age_days",
                            "render_on_stop",
                        )
                    }
                )
            except Exception:
                log.exception("Failed to roll back timelapse defaults")
        status = 400 if isinstance(exc, (TypeError, ValueError)) else 500
        return jsonify({"error": str(exc)}), status
    return jsonify({"ok": True, "settings": defaults})


@features_bp.post("/timelapse/prune")
def timelapse_prune():
    return jsonify({"ok": True, "deleted": _timelapse().prune()})
