"""Stream and snapshot HTTP endpoints."""

import logging

from flask import Blueprint, Response, abort, current_app, request, send_file

log = logging.getLogger(__name__)
stream_bp = Blueprint("stream", __name__)

_STREAM_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


@stream_bp.route("/stream", methods=["GET", "HEAD"])
def mjpeg_stream():
    """MJPEG multipart stream — open in browser or VLC."""
    if request.method == "HEAD":
        # Return headers only; never touch the generator so the client count stays clean.
        return Response(
            status=200,
            mimetype="multipart/x-mixed-replace; boundary=bambucam_frame",
            headers=_STREAM_HEADERS,
        )
    mjpeg = current_app.config["mjpeg_streamer"]
    return Response(
        mjpeg.generate(),
        mimetype="multipart/x-mixed-replace; boundary=bambucam_frame",
        headers=_STREAM_HEADERS,
    )


@stream_bp.get("/snapshot")
def snapshot():
    """Capture a JPEG snapshot and optionally save it to disk."""
    snapshot_svc = current_app.config["snapshot_service"]
    save = request.args.get("save", "false").lower() in {"1", "true", "yes", "on"}
    try:
        frame = snapshot_svc.capture(save=save)
        return Response(
            frame,
            mimetype="image/jpeg",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Content-Disposition": "inline; filename=snapshot.jpg",
                "X-BambuCam-Saved": "true" if save else "false",
            },
        )
    except Exception as exc:
        log.error("Snapshot failed: %s", exc)
        return Response(f"Error: {exc}", status=500, mimetype="text/plain")


@stream_bp.get("/snapshots/<path:filename>")
def saved_snapshot(filename: str):
    """Download or display one safely resolved saved snapshot."""
    snapshot_svc = current_app.config["snapshot_service"]
    try:
        path = snapshot_svc.resolve_snapshot(filename)
    except ValueError:
        abort(400)
    except FileNotFoundError:
        abort(404)

    return send_file(
        path,
        mimetype="image/jpeg",
        as_attachment=request.args.get("download", "false").lower() == "true",
        download_name=path.name,
        conditional=True,
        max_age=0,
    )
