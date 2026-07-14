"""Operational API endpoints: diagnostics and snapshot retention."""

from datetime import datetime, timezone

from flask import Blueprint, Response, current_app, jsonify, request

from bambucam.observability import diagnostics_payload, diagnostics_zip

operations_bp = Blueprint("operations", __name__)


def _services() -> tuple:
    return (
        current_app.config["bambucam_config"],
        current_app.config["camera_manager"],
        current_app.config["mjpeg_streamer"],
        current_app.config["rtsp_streamer"],
        current_app.config["snapshot_service"],
        current_app.config["updater"],
    )


@operations_bp.get("/diagnostics")
def diagnostics():
    """Return a redacted support payload as JSON."""
    return jsonify(diagnostics_payload(*_services()))


@operations_bp.get("/diagnostics/download")
def diagnostics_download():
    """Download diagnostics.json and recent logs as a ZIP archive."""
    payload = diagnostics_payload(*_services())
    archive = diagnostics_zip(payload)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Response(
        archive,
        mimetype="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="bambucam_diagnostics_{timestamp}.zip"',
            "Cache-Control": "no-store",
        },
    )


@operations_bp.get("/snapshot/retention")
def snapshot_retention_status():
    return jsonify(current_app.config["snapshot_service"].retention_status())


@operations_bp.post("/snapshot/retention")
def snapshot_retention_update():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "JSON body must be an object"}), 400

    allowed = {"max_count", "max_age_days", "max_bytes"}
    unknown = set(data) - allowed
    if unknown:
        return jsonify({"error": f"Unknown retention field(s): {', '.join(sorted(unknown))}"}), 400

    try:
        values = {}
        for key, value in data.items():
            parsed = int(value)
            if parsed < 0:
                raise ValueError(f"{key} must be zero or greater")
            values[key] = parsed
        status = current_app.config["snapshot_service"].update_retention(**values)
        config = current_app.config["bambucam_config"]
        config.update_section("streaming", {"snapshot": values})
        config.save()
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "retention": status})


@operations_bp.post("/snapshot/prune")
def snapshot_prune():
    deleted = current_app.config["snapshot_service"].prune()
    return jsonify({"ok": True, "deleted": deleted, "deleted_count": len(deleted)})
