"""Flask application factory for BambuCam."""

import atexit
import logging
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

if TYPE_CHECKING:
    from bambucam.camera.manager import CameraManager
    from bambucam.config import Config
    from bambucam.streaming.mjpeg import MJPEGStreamer
    from bambucam.streaming.rtsp import RTSPStreamer
    from bambucam.streaming.snapshot import SnapshotService
    from bambucam.updater import Updater

log = logging.getLogger(__name__)


def create_app(
    config: "Config",
    camera_manager: "CameraManager",
    mjpeg_streamer: "MJPEGStreamer",
    rtsp_streamer: "RTSPStreamer",
    snapshot_service: "SnapshotService",
    updater: "Updater",
) -> Flask:
    """Create the Flask app and wire together shared services."""
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )

    secret = config.get("web", "secret_key")
    if not secret:
        secret = secrets.token_hex(32)
        config.set("web", "secret_key", value=secret)
        config.save()
        log.info("Generated and persisted a WebUI session secret")

    app.config.update(
        SECRET_KEY=secret,
        MAX_CONTENT_LENGTH=1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=bool(config.get("web", "https", "enabled", default=False)),
    )

    if config.get("web", "trust_proxy", default=False):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    from bambucam.camera.profiles import CameraProfileService
    from bambucam.timelapse import TimelapseService

    timelapse_config = config.get("streaming", "timelapse", default={}) or {}
    profile_service = CameraProfileService(
        config=config,
        camera=camera_manager,
        mjpeg=mjpeg_streamer,
        rtsp=rtsp_streamer,
    )
    timelapse_service = TimelapseService(
        capture_fn=lambda: camera_manager.capture_jpeg(quality=95),
        root_dir=Path(
            timelapse_config.get("save_dir", "/var/lib/bambucam/timelapse")
        ),
        ffmpeg_path=config.get("system", "ffmpeg_path", default="ffmpeg"),
        interval_seconds=timelapse_config.get("interval_seconds", 10),
        output_fps=timelapse_config.get("output_fps", 30),
        max_sessions=timelapse_config.get("max_sessions", 20),
        max_age_days=timelapse_config.get("max_age_days", 90),
        render_on_stop=timelapse_config.get("render_on_stop", True),
    )
    atexit.register(timelapse_service.shutdown)

    app.config["bambucam_config"] = config
    app.config["camera_manager"] = camera_manager
    app.config["mjpeg_streamer"] = mjpeg_streamer
    app.config["rtsp_streamer"] = rtsp_streamer
    app.config["snapshot_service"] = snapshot_service
    app.config["updater"] = updater
    app.config["camera_profile_service"] = profile_service
    app.config["timelapse_service"] = timelapse_service

    from bambucam.observability import install_log_buffer
    from bambucam.web.api import api_bp
    from bambucam.web.features import features_bp
    from bambucam.web.operations import operations_bp
    from bambucam.web.security import configure_web_security
    from bambucam.web.stream import stream_bp
    from bambucam.web.ui import ui_bp

    install_log_buffer(capacity=int(config.get("system", "diagnostics_log_lines", default=300)))
    configure_web_security(app, config)
    app.register_blueprint(api_bp, url_prefix="/api/v1")
    app.register_blueprint(operations_bp, url_prefix="/api/v1")
    app.register_blueprint(features_bp, url_prefix="/api/v1")
    app.register_blueprint(stream_bp)
    app.register_blueprint(ui_bp)

    log.info("Flask app created")
    return app
