"""Stream and snapshot HTTP endpoints."""

import logging

from flask import Blueprint, Response, current_app, request

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
    """Single JPEG snapshot."""
    snapshot_svc = current_app.config["snapshot_service"]
    save = False
    try:
        frame = snapshot_svc.capture(save=save)
        return Response(
            frame,
            mimetype="image/jpeg",
            headers={
                "Cache-Control": "no-cache",
                "Content-Disposition": "inline; filename=snapshot.jpg",
            },
        )
    except Exception as e:
        log.error("Snapshot failed: %s", e)
        return Response(f"Error: {e}", status=500)
