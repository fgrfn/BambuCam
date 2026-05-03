"""
Flask application factory.

Creates the Flask app and registers all blueprints (API, stream, UI).
"""

import logging
import os
import secrets
from typing import TYPE_CHECKING

from flask import Flask

if TYPE_CHECKING:
    from bambucam.camera.manager import CameraManager
    from bambucam.streaming.mjpeg import MJPEGStreamer
    from bambucam.streaming.rtsp import RTSPStreamer
    from bambucam.streaming.snapshot import SnapshotService
    from bambucam.config import Config

log = logging.getLogger(__name__)


def create_app(
    config: "Config",
    camera_manager: "CameraManager",
    mjpeg_streamer: "MJPEGStreamer",
    rtsp_streamer: "RTSPStreamer",
    snapshot_service: "SnapshotService",
) -> Flask:
    """Application factory — wire together all components."""

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )

    # Secret key for session cookies
    secret = config.get("web", "secret_key") or secrets.token_hex(32)
    app.config["SECRET_KEY"] = secret

    # Store shared services in app context
    app.config["bambucam_config"] = config
    app.config["camera_manager"] = camera_manager
    app.config["mjpeg_streamer"] = mjpeg_streamer
    app.config["rtsp_streamer"] = rtsp_streamer
    app.config["snapshot_service"] = snapshot_service

    from bambucam.web.api import api_bp
    from bambucam.web.stream import stream_bp
    from bambucam.web.ui import ui_bp

    app.register_blueprint(api_bp, url_prefix="/api/v1")
    app.register_blueprint(stream_bp)
    app.register_blueprint(ui_bp)

    log.info("Flask app created")
    return app
