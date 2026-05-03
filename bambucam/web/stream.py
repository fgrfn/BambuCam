"""Stream and snapshot HTTP endpoints."""

import logging

from flask import Blueprint, Response, current_app, send_file

log = logging.getLogger(__name__)
stream_bp = Blueprint("stream", __name__)


@stream_bp.get("/stream")
def mjpeg_stream():
    """MJPEG multipart stream — open in browser or VLC."""
    mjpeg = current_app.config["mjpeg_streamer"]
    return Response(
        mjpeg.generate(),
        mimetype="multipart/x-mixed-replace; boundary=bambucam_frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
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
