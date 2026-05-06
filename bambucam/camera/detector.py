"""Camera auto-detection for Raspberry Pi and USB webcams."""

import logging
import os
import re
import subprocess
from typing import Optional

from bambucam.camera.models import (
    CAMERA_USB_GENERIC,
    CameraModel,
    Resolution,
    get_model_by_sensor,
)

log = logging.getLogger(__name__)


class DetectedCamera:
    """Result of a camera detection scan."""

    def __init__(
        self,
        device: str,
        model: CameraModel,
        backend: str,
        index: int = 0,
        detected_resolutions: Optional[list[Resolution]] = None,
    ):
        self.device = device  # e.g. "/dev/video0" or "libcamera:0"
        self.model = model
        self.backend = backend  # "picamera2" or "v4l2"
        self.index = index
        self.detected_resolutions = detected_resolutions or model.supported_resolutions

    def __repr__(self) -> str:
        return f"<DetectedCamera {self.model.name!r} backend={self.backend!r} device={self.device!r}>"  # noqa: E501


def detect_cameras() -> list[DetectedCamera]:
    """
    Scan the system for available cameras.
    Tries libcamera/picamera2 first, then falls back to V4L2 USB devices.
    """
    cameras: list[DetectedCamera] = []

    libcam = _detect_libcamera()
    if libcam:
        cameras.extend(libcam)
        log.info("libcamera detected %d camera(s)", len(libcam))

    usb = _detect_v4l2()
    # Avoid double-counting: CSI cameras already appear as /dev/videoN via libcamera
    known_devices = {c.device for c in cameras}
    for cam in usb:
        if cam.device not in known_devices:
            cameras.append(cam)

    if not cameras:
        log.warning("No cameras detected on this system")

    return cameras


# ---------------------------------------------------------------------------
# libcamera / picamera2
# ---------------------------------------------------------------------------


def _detect_libcamera() -> list[DetectedCamera]:
    """Use rpicam-hello or libcamera-hello --list-cameras to enumerate CSI cameras."""
    for cmd in ["rpicam-hello", "libcamera-hello"]:
        try:
            result = subprocess.run(
                [cmd, "--list-cameras"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = result.stdout + result.stderr
            if "Available cameras" in output or re.search(r"\d+\s*:\s*\w+\s*\[", output):
                log.debug("libcamera detected via %s", cmd)
                return _parse_libcamera_output(output)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    log.debug("Neither rpicam-hello nor libcamera-hello available")
    return []


def _parse_libcamera_output(output: str) -> list[DetectedCamera]:
    cameras = []
    # Each camera block starts with: "0 : imx219 [...]"
    for match in re.finditer(r"(\d+)\s*:\s*(\w+)\s*\[", output):
        idx = int(match.group(1))
        sensor = match.group(2).lower()
        model = get_model_by_sensor(sensor)
        if model is None:
            log.warning("Unknown libcamera sensor: %s — using generic model", sensor)
            from bambucam.camera.models import CAMERA_V2

            model = CAMERA_V2

        # Try to extract resolutions from the libcamera output block
        resolutions = _parse_libcamera_resolutions(output, sensor)

        cameras.append(
            DetectedCamera(
                device=f"libcamera:{idx}",
                model=model,
                backend="picamera2",
                index=idx,
                detected_resolutions=resolutions or model.supported_resolutions,
            )
        )
    return cameras


def _parse_libcamera_resolutions(output: str, sensor: str) -> list[Resolution]:
    """Extract supported resolutions from libcamera output."""
    resolutions = []
    in_sensor_block = False
    for line in output.splitlines():
        if sensor in line.lower():
            in_sensor_block = True
        if in_sensor_block:
            # Match patterns like "1920x1080" or "3280x2464"
            for m in re.finditer(r"(\d{3,4})x(\d{3,4})", line):
                r = Resolution(int(m.group(1)), int(m.group(2)))
                if r not in resolutions:
                    resolutions.append(r)
    return resolutions


# ---------------------------------------------------------------------------
# V4L2 / USB webcams
# ---------------------------------------------------------------------------


def _detect_v4l2() -> list[DetectedCamera]:
    """Scan /dev/video* for V4L2 capture devices."""
    cameras = []
    video_devices = sorted(p for p in os.listdir("/dev") if re.match(r"video\d+$", p))
    for dev_name in video_devices:
        device = f"/dev/{dev_name}"
        info = _v4l2_device_info(device)
        if info is None:
            continue
        driver, card, capabilities = info
        # 0x00000001 = V4L2_CAP_VIDEO_CAPTURE
        if not (capabilities & 0x00000001):
            continue
        # Skip metadata/ISP nodes (they aren't capture devices for us)
        if any(kw in card.lower() for kw in ("isp", "unicam", "bcm2835")):
            continue

        resolutions = _v4l2_resolutions(device)
        model = _match_usb_model(card, resolutions)
        cameras.append(
            DetectedCamera(
                device=device,
                model=model,
                backend="v4l2",
                index=int(re.search(r"\d+$", dev_name).group()),
                detected_resolutions=resolutions or model.supported_resolutions,
            )
        )
        log.info("V4L2 camera found: %s (%s)", device, card)
    return cameras


def _v4l2_device_info(device: str) -> Optional[tuple[str, str, int]]:
    """Return (driver, card, capabilities) or None."""
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--device", device, "--info"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        output = result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    driver = _extract_v4l2_field(output, "Driver name")
    card = _extract_v4l2_field(output, "Card type")
    cap_str = _extract_v4l2_field(output, "Device Caps")
    try:
        capabilities = int(cap_str, 16) if cap_str else 0
    except ValueError:
        capabilities = 0
    return driver, card, capabilities


def _extract_v4l2_field(output: str, field: str) -> str:
    m = re.search(rf"{re.escape(field)}\s*:\s*(.+)", output)
    return m.group(1).strip() if m else ""


def _v4l2_resolutions(device: str) -> list[Resolution]:
    """Query supported resolutions via v4l2-ctl."""
    resolutions = []
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--device", device, "--list-formats-ext"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for m in re.finditer(r"Size: Discrete (\d+)x(\d+)", result.stdout):
            r = Resolution(int(m.group(1)), int(m.group(2)))
            if r not in resolutions:
                resolutions.append(r)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return resolutions


def _match_usb_model(card: str, resolutions: list[Resolution]) -> CameraModel:
    """Try to match a known USB camera model by card name, else use generic."""
    # Extend here with known USB camera models in the future
    return CAMERA_USB_GENERIC
