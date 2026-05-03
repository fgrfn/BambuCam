"""
BambuCam entry point.

Usage:
  bambucam                     # start with config auto-discovery
  bambucam --config /path/to/bambucam.yaml
  bambucam --list-cameras      # detect and print cameras
  bambucam --help
"""

import argparse
import logging
import sys
from pathlib import Path


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _parse_args() -> argparse.Namespace:
    from bambucam import __version__

    p = argparse.ArgumentParser(
        prog="bambucam",
        description="BambuCam — Raspberry Pi camera streaming for BambuBuddy",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--config", type=Path, help="Path to config YAML file")
    p.add_argument(
        "--list-cameras",
        action="store_true",
        help="Detect cameras, print results, and exit",
    )
    p.add_argument("--log-level", default="INFO", help="Log level (DEBUG/INFO/WARNING/ERROR)")
    p.add_argument("--host", help="Override WebUI host (default: 0.0.0.0)")
    p.add_argument("--port", type=int, help="Override WebUI port (default: 8080)")
    p.add_argument("--no-rtsp", action="store_true", help="Disable RTSP streaming")
    p.add_argument("--no-mjpeg", action="store_true", help="Disable MJPEG streaming")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    _setup_logging(args.log_level)
    log = logging.getLogger("bambucam")

    log.info("BambuCam starting up…")

    from bambucam.config import get_config

    cfg = get_config()
    cfg.load(args.config)

    if args.log_level:
        _setup_logging(args.log_level)

    # -- List cameras and exit ------------------------------------------------
    if args.list_cameras:
        from bambucam.camera.detector import detect_cameras

        cameras = detect_cameras()
        if not cameras:
            print("No cameras detected.")
            sys.exit(1)
        print(f"Found {len(cameras)} camera(s):")
        for i, cam in enumerate(cameras):
            print(f"\n  [{i}] {cam.model.name}")
            print(f"      Sensor  : {cam.model.sensor}")
            print(f"      Backend : {cam.backend}")
            print(f"      Device  : {cam.device}")
            print(f"      Max res : {cam.model.max_resolution}")
            print("      Features: ", end="")
            feats = []
            if cam.model.has_autofocus:
                feats.append("Autofocus")
            if cam.model.has_hdr:
                feats.append("HDR")
            if cam.model.is_noir:
                feats.append("NoIR")
            if cam.model.has_global_shutter:
                feats.append("Global Shutter")
            print(", ".join(feats) if feats else "—")
        sys.exit(0)

    # -- Normal startup -------------------------------------------------------
    from bambucam.camera.manager import CameraManager
    from bambucam.camera.models import Resolution
    from bambucam.streaming.mjpeg import MJPEGStreamer
    from bambucam.streaming.rtsp import RTSPStreamer
    from bambucam.streaming.snapshot import SnapshotService
    from bambucam.updater import Updater
    from bambucam.web.app import create_app

    cam_cfg = cfg.camera
    stream_cfg = cfg.streaming
    web_cfg = cfg.web

    # Camera
    camera = CameraManager()
    _camera_ok = True
    try:
        detected = camera.detect_and_select(cam_cfg.get("index", 0))
    except RuntimeError as e:
        log.warning("No camera detected: %s — starting in headless mode (WebUI only)", e)
        _camera_ok = False

    if _camera_ok:
        try:
            camera.setup(
                detected=detected,
                resolution=Resolution.from_string(cam_cfg.get("resolution", "1920x1080")),
                framerate=cam_cfg.get("framerate", 15),
                settings={
                    k: cam_cfg[k]
                    for k in (
                        "vflip",
                        "hflip",
                        "brightness",
                        "contrast",
                        "saturation",
                        "sharpness",
                        "exposure_mode",
                        "awb_mode",
                        "autofocus",
                        "hdr",
                    )
                    if k in cam_cfg
                },
            )
            camera.start()
        except Exception as e:
            log.error("Failed to start camera: %s — continuing in headless mode", e)
            _camera_ok = False

    # MJPEG streamer
    mjpeg_cfg = stream_cfg.get("mjpeg", {})
    mjpeg = MJPEGStreamer(
        capture_fn=camera.capture_jpeg if _camera_ok else lambda: None,
        target_fps=mjpeg_cfg.get("fps", 15),
    )
    if _camera_ok and not args.no_mjpeg and mjpeg_cfg.get("enabled", True):
        mjpeg.start()

    # RTSP streamer
    rtsp_cfg = stream_cfg.get("rtsp", {})
    rtsp_auth = rtsp_cfg.get("auth", {})
    rtsp = RTSPStreamer(
        v4l2_device=(camera.v4l2_device if _camera_ok else None) or "/dev/video0",
        resolution=cam_cfg.get("resolution", "1920x1080"),
        framerate=cam_cfg.get("framerate", 15),
        bitrate_kbps=rtsp_cfg.get("bitrate_kbps", 2000),
        stream_name=rtsp_cfg.get("stream_name", "cam"),
        mediamtx_path=Path(cfg.system.get("mediamtx_path", "/usr/local/bin/mediamtx")),
        enable_hls=rtsp_cfg.get("enable_hls", True),
        enable_webrtc=rtsp_cfg.get("enable_webrtc", False),
        rtsp_auth_user=rtsp_auth.get("username") if rtsp_auth.get("enabled") else None,
        rtsp_auth_pass=rtsp_auth.get("password") if rtsp_auth.get("enabled") else None,
    )
    if _camera_ok and not args.no_rtsp and rtsp_cfg.get("enabled", True):
        try:
            rtsp.start()
        except FileNotFoundError:
            log.warning(
                "MediaMTX not found — RTSP streaming disabled. "
                "Run the installer or: bambucam-install"
            )

    # Snapshot service
    snapshot = SnapshotService(
        capture_fn=camera.capture_jpeg if _camera_ok else lambda: None,
        snapshot_dir=Path(
            stream_cfg.get("snapshot", {}).get("save_dir", "/var/lib/bambucam/snapshots")
        ),
    )

    # Updater
    from bambucam import __version__

    updater = Updater(
        current_version=__version__,
        include_prerelease=cfg.get("system", "update_include_prerelease", default=False),
    )

    # Flask app
    host = args.host or web_cfg.get("host", "0.0.0.0")
    port = args.port or web_cfg.get("port", 8080)

    app = create_app(
        config=cfg,
        camera_manager=camera,
        mjpeg_streamer=mjpeg,
        rtsp_streamer=rtsp,
        snapshot_service=snapshot,
        updater=updater,
    )

    log.info("WebUI listening on http://%s:%d", host, port)
    log.info("MJPEG stream: http://<pi-ip>:%d/stream", port)
    if rtsp.is_running:
        log.info(
            "RTSP stream: rtsp://<pi-ip>:%d/%s",
            rtsp_cfg.get("port", 8554),
            rtsp_cfg.get("stream_name", "cam"),
        )

    try:
        app.run(host=host, port=port, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        log.info("Shutting down…")
    finally:
        rtsp.stop()
        mjpeg.stop()
        camera.stop()
        log.info("BambuCam stopped.")


if __name__ == "__main__":
    main()
