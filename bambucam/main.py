"""
BambuCam entry point.

Usage:
  bambucam
  bambucam --config /path/to/bambucam.yaml
  bambucam --list-cameras
"""

import argparse
import logging
import sys
from pathlib import Path


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )


def _parse_args() -> argparse.Namespace:
    from bambucam import __version__

    parser = argparse.ArgumentParser(
        prog="bambucam",
        description="BambuCam — Raspberry Pi camera streaming for BambuBuddy",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, help="Path to config YAML file")
    parser.add_argument(
        "--list-cameras",
        action="store_true",
        help="Detect cameras, print results, and exit",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Override configured log level (DEBUG/INFO/WARNING/ERROR)",
    )
    parser.add_argument("--host", help="Override WebUI host")
    parser.add_argument("--port", type=int, help="Override WebUI port")
    parser.add_argument("--no-rtsp", action="store_true", help="Disable RTSP streaming")
    parser.add_argument("--no-mjpeg", action="store_true", help="Disable MJPEG streaming")
    return parser.parse_args()


def _best_resolution_and_fps(model, tier_fps_cap=None):
    """Pick the model mode with the highest width × height × FPS score."""
    candidates = model.resolution_max_framerates
    if not candidates:
        fps = model.max_framerate
        if tier_fps_cap is not None:
            fps = min(fps, tier_fps_cap)
        return model.max_resolution, fps

    best_resolution, best_fps, best_score = None, None, -1
    for resolution, candidate_fps in candidates.items():
        fps = min(candidate_fps, tier_fps_cap) if tier_fps_cap is not None else candidate_fps
        score = resolution.width * resolution.height * fps
        if score > best_score:
            best_resolution, best_fps, best_score = resolution, fps, score
    return best_resolution, best_fps


def _is_auto(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in {"", "auto"})


def _resolve_camera_mode(model, camera_config: dict, tier_fps_cap=None, resolutions=None):
    """Resolve auto/explicit camera mode and enforce model and hardware limits."""
    from bambucam.camera.models import Resolution

    smart_resolution, smart_fps = _best_resolution_and_fps(model, tier_fps_cap)
    available = list(resolutions or model.supported_resolutions)
    configured_resolution = camera_config.get("resolution", "auto")

    if _is_auto(configured_resolution):
        resolution = smart_resolution
        if available and resolution not in available:
            # Generic USB models do not necessarily know the modes reported by
            # the attached device. Prefer the largest actually detected mode.
            resolution = max(available, key=lambda item: item.width * item.height)
    else:
        resolution = Resolution.from_string(str(configured_resolution))

    if available and resolution not in available:
        allowed = ", ".join(str(item) for item in available)
        raise ValueError(f"Resolution {resolution} is not supported. Available: {allowed}")

    max_fps = model.resolution_max_framerates.get(resolution, model.max_framerate)
    if tier_fps_cap is not None:
        max_fps = min(max_fps, int(tier_fps_cap))

    configured_fps = camera_config.get("framerate", "auto")
    if _is_auto(configured_fps):
        fps = smart_fps if resolution == smart_resolution else max_fps
    else:
        try:
            fps = int(configured_fps)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid camera framerate: {configured_fps!r}") from exc
        if fps < 1:
            raise ValueError("Camera framerate must be at least 1 FPS")
        if fps > max_fps:
            logging.getLogger(__name__).warning(
                "Requested %d FPS at %s exceeds the effective maximum of %d; capping",
                fps,
                resolution,
                max_fps,
            )
            fps = max_fps

    return resolution, int(fps)


def _effective_mjpeg_fps(camera_fps: int, mjpeg_cfg: dict, tier_fps_cap=None) -> int:
    """Return configured MJPEG FPS capped by camera and hardware limits."""
    try:
        requested_fps = int(mjpeg_cfg.get("fps", camera_fps))
    except (TypeError, ValueError):
        requested_fps = int(camera_fps)

    effective_fps = min(requested_fps, int(camera_fps))
    if tier_fps_cap is not None:
        effective_fps = min(effective_fps, int(tier_fps_cap))
    return max(1, effective_fps)


def main() -> None:
    args = _parse_args()
    _setup_logging("INFO")

    from bambucam.config import Config

    cfg = Config()
    cfg.load(args.config)
    _setup_logging(args.log_level or cfg.system.get("log_level", "INFO"))
    log = logging.getLogger("bambucam")
    log.info("BambuCam starting up…")

    if args.list_cameras:
        from bambucam.camera.detector import detect_cameras

        cameras = detect_cameras()
        if not cameras:
            print("No cameras detected.")
            sys.exit(1)
        print(f"Found {len(cameras)} camera(s):")
        for position, camera in enumerate(cameras):
            print(f"\n  [{position}] {camera.model.name}")
            print(f"      Sensor  : {camera.model.sensor}")
            print(f"      Backend : {camera.backend}")
            print(f"      Device  : {camera.device}")
            print(f"      Modes   : {', '.join(str(r) for r in camera.detected_resolutions)}")
            features = []
            if camera.model.has_autofocus:
                features.append("Autofocus")
            if camera.model.has_hdr:
                features.append("HDR")
            if camera.model.is_noir:
                features.append("NoIR")
            if camera.model.has_global_shutter:
                features.append("Global Shutter")
            print(f"      Features: {', '.join(features) if features else '—'}")
        sys.exit(0)

    from bambucam.camera.manager import CameraManager
    from bambucam.streaming.mjpeg import MJPEGStreamer
    from bambucam.streaming.rtsp import RTSPStreamer
    from bambucam.streaming.snapshot import SnapshotService
    from bambucam.updater import Updater
    from bambucam.web.app import create_app

    camera_config = cfg.camera
    streaming_config = cfg.streaming
    web_config = cfg.web

    from bambucam.system_info import pi_capability_tier

    tier = pi_capability_tier()
    tier_label = {
        1: "low (Pi Zero/1/2) — MJPEG-only",
        2: "mid (Pi Zero 2 W / Pi 3) — RTSP + MJPEG≤30fps",
        3: "high (Pi 4/5+) — full stack",
    }.get(tier, str(tier))
    log.info("Hardware capability tier %d: %s", tier, tier_label)

    rtsp_default_enabled = tier >= 2
    mjpeg_fps_cap = {1: 15, 2: 30}.get(tier)

    camera = CameraManager()
    camera_ok = True
    detected = None
    selected_resolution = None
    selected_fps = 15
    try:
        detected = camera.detect_and_select(
            int(camera_config.get("index", 0)),
            module_override=camera_config.get("module", "auto"),
            preferred_backend=camera_config.get("backend", "auto"),
        )
        selected_resolution, selected_fps = _resolve_camera_mode(
            detected.model,
            camera_config,
            mjpeg_fps_cap,
            detected.detected_resolutions,
        )
        log.info("Selected camera mode: %s @ %d FPS", selected_resolution, selected_fps)
    except (RuntimeError, ValueError) as exc:
        log.warning("Camera unavailable: %s — starting in headless mode", exc)
        camera_ok = False

    rtsp_config = streaming_config.get("rtsp", {})
    will_use_rtsp = (
        camera_ok
        and not args.no_rtsp
        and rtsp_config.get("enabled", rtsp_default_enabled)
    )

    if camera_ok and detected is not None and selected_resolution is not None:
        try:
            camera.setup(
                detected=detected,
                resolution=selected_resolution,
                framerate=selected_fps,
                settings={
                    key: camera_config[key]
                    for key in (
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
                        "noise_reduction",
                    )
                    if key in camera_config
                },
                enable_lores=will_use_rtsp,
            )
            camera.start()
        except Exception as exc:
            log.error("Failed to start camera: %s — continuing in headless mode", exc)
            camera_ok = False
            will_use_rtsp = False

    mjpeg_config = streaming_config.get("mjpeg", {})
    mjpeg_fps = _effective_mjpeg_fps(selected_fps, mjpeg_config, mjpeg_fps_cap)
    if camera_ok:
        camera.set_jpeg_quality(int(mjpeg_config.get("quality", 85)))

    mjpeg = MJPEGStreamer(
        capture_fn=camera.capture_jpeg if camera_ok else lambda: None,
        target_fps=mjpeg_fps,
    )
    if camera_ok and not args.no_mjpeg and mjpeg_config.get("enabled", True):
        mjpeg.start()

    rtsp_auth = rtsp_config.get("auth", {})
    picamera2_backend = None
    if will_use_rtsp and camera_ok and camera.backend is not None:
        from bambucam.camera.backends.picamera2_backend import Picamera2Backend

        if isinstance(camera.backend, Picamera2Backend):
            picamera2_backend = camera.backend

    rtsp = RTSPStreamer(
        v4l2_device=(camera.v4l2_device if camera_ok else None) or "/dev/video0",
        resolution=str(selected_resolution or "1920x1080"),
        framerate=selected_fps,
        bitrate_kbps=rtsp_config.get("bitrate_kbps", 2000),
        stream_name=rtsp_config.get("stream_name", "cam"),
        mediamtx_path=Path(cfg.system.get("mediamtx_path", "/usr/local/bin/mediamtx")),
        enable_hls=rtsp_config.get("enable_hls", True),
        enable_webrtc=rtsp_config.get("enable_webrtc", False),
        rtsp_auth_user=rtsp_auth.get("username") if rtsp_auth.get("enabled") else None,
        rtsp_auth_pass=rtsp_auth.get("password") if rtsp_auth.get("enabled") else None,
        camera_backend=picamera2_backend,
    )
    if will_use_rtsp:
        try:
            rtsp.start()
        except FileNotFoundError:
            log.warning("MediaMTX not found — RTSP streaming disabled. Run the installer.")
        except Exception as exc:
            log.warning("RTSP streaming disabled: %s", exc)

    snapshot = SnapshotService(
        capture_fn=(lambda: camera.capture_jpeg(quality=95)) if camera_ok else lambda: None,
        snapshot_dir=Path(
            streaming_config.get("snapshot", {}).get(
                "save_dir", "/var/lib/bambucam/snapshots"
            )
        ),
    )

    from bambucam import __version__

    updater = Updater(
        current_version=__version__,
        include_prerelease=cfg.get("system", "update_include_prerelease", default=False),
    )

    host = args.host or web_config.get("host", "0.0.0.0")
    port = args.port or int(web_config.get("port", 8080))
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
        log.info("RTSP stream: %s", rtsp.stream_urls("<pi-ip>")["rtsp"])

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
