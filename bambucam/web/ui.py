"""UI, health, and Prometheus routes."""

from flask import Blueprint, Response, current_app, jsonify, render_template, url_for

from bambucam.observability import health_payload, prometheus_payload

ui_bp = Blueprint("ui", __name__)


@ui_bp.get("/")
def index():
    """Render the dashboard and load optional feature modules as static assets."""
    html = render_template("index.html")
    assets = (
        f'<link rel="stylesheet" href="{url_for("static", filename="css/features.css")}" />\n'
        f'<script src="{url_for("static", filename="js/features.js")}" defer></script>\n'
    )
    return html.replace("</head>", f"{assets}</head>", 1)


@ui_bp.get("/health")
def health():
    """Public liveness/readiness probe with a degraded HTTP status when needed."""
    payload, status_code = health_payload(
        current_app.config["camera_manager"],
        current_app.config["mjpeg_streamer"],
        current_app.config["rtsp_streamer"],
    )
    return jsonify(payload), status_code


@ui_bp.get("/metrics")
def metrics():
    """Prometheus text exposition endpoint (protected when auth is enabled)."""
    payload = prometheus_payload(
        current_app.config["bambucam_config"],
        current_app.config["camera_manager"],
        current_app.config["mjpeg_streamer"],
        current_app.config["rtsp_streamer"],
        current_app.config["snapshot_service"],
        current_app.config["updater"],
    )
    return Response(
        payload,
        mimetype="text/plain; version=0.0.4; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )
