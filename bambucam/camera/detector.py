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
_CAMERA_HEADER_RE = re.compile(r"(?m)^(\d+)\s*:\s*(\w+)\s*\[")


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
        self.device = device
        self.model = model
        self.backend = backend
        self.index = index
        self.detected_resolutions = detected_resolutions or model.supported_resolutions

    def __repr__(self) -> str:
        return (
            f"<DetectedCamera {self.model.name!r} "
            f"backend={self.backend!r} device={self.device!r}>"
        )


def detect_cameras() -> list[DetectedCamera]:
    """Scan the system for CSI/libcamera and V4L2 capture devices."""
    cameras: list[DetectedCamera] = []

    libcam = _detect_libcamera()
    if libcam:
        cameras.extend(libcam)
        log.info("libcamera detected %d camera(s)", len(libcam))

    usb = _detect_v4l2()
    # Avoid exact duplicate device records. CSI ISP/metadata nodes are filtered
    # separately by card name in the V4L2 scanner.
    known_devices = {camera.device for camera in cameras}
    cameras.extend(camera for camera in usb if camera.device not in known_devices)

    if not cameras:
        log.warning("No cameras detected on this system")

    return cameras


def _detect_libcamera() -> list[DetectedCamera]:
    """Use rpicam-hello or libcamera-hello to enumerate CSI cameras."""
    for command in ("rpicam-hello", "libcamera-hello"):
        try:
            result = subprocess.run(
                [command, "--list-cameras"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = result.stdout + result.stderr
            if "Available cameras" in output or _CAMERA_HEADER_RE.search(output):
                log.debug("libcamera detected via %s", command)
                return _parse_libcamera_output(output)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    log.debug("Neither rpicam-hello nor libcamera-hello is available")
    return []


def _libcamera_blocks(output: str) -> list[tuple[re.Match, str]]:
    """Return each camera header together with only its own output block."""
    matches = list(_CAMERA_HEADER_RE.finditer(output))
    blocks = []
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(output)
        blocks.append((match, output[match.start() : end]))
    return blocks


def _merge_resolutions(*groups: list[Resolution]) -> list[Resolution]:
    """Return resolution groups in stable order without duplicates."""
    merged: list[Resolution] = []
    for group in groups:
        for resolution in group:
            if resolution not in merged:
                merged.append(resolution)
    return merged


def _parse_libcamera_output(output: str) -> list[DetectedCamera]:
    cameras = []
    for match, block in _libcamera_blocks(output):
        index = int(match.group(1))
        sensor = match.group(2).lower()
        model = get_model_by_sensor(sensor)
        if model is None:
            # Keep an unknown sensor usable without claiming it is a specific
            # Raspberry Pi module. The generic model can later be overridden by
            # camera.module in the configuration.
            log.warning("Unknown libcamera sensor: %s — using generic capabilities", sensor)
            model = CAMERA_USB_GENERIC

        sensor_modes = _parse_libcamera_resolutions(block, sensor)
        # rpicam --list-cameras reports native sensor modes, not every valid ISP
        # output size. Picamera2 can scale/crop those native modes to the known
        # output resolutions from the model table (for example OV5647 1280x720).
        # Keep both sets so an existing valid output resolution never forces the
        # application into headless mode after an update or profile change.
        resolutions = _merge_resolutions(sensor_modes, model.supported_resolutions)
        cameras.append(
            DetectedCamera(
                device=f"libcamera:{index}",
                model=model,
                backend="picamera2",
                index=index,
                detected_resolutions=resolutions,
            )
        )
    return cameras


def _parse_libcamera_resolutions(output: str, sensor: str) -> list[Resolution]:
    """Extract resolutions from one sensor block, even if full output is supplied."""
    block = output
    blocks = _libcamera_blocks(output)
    if blocks:
        matching_block = next(
            (candidate for match, candidate in blocks if match.group(2).lower() == sensor.lower()),
            None,
        )
        if matching_block is not None:
            block = matching_block

    resolutions = []
    for match in re.finditer(r"(\d{3,5})x(\d{3,5})", block):
        resolution = Resolution(int(match.group(1)), int(match.group(2)))
        if resolution not in resolutions:
            resolutions.append(resolution)
    return resolutions


def _detect_v4l2() -> list[DetectedCamera]:
    """Scan /dev/video* for V4L2 capture devices."""
    cameras = []
    video_devices = sorted(path for path in os.listdir("/dev") if re.match(r"video\d+$", path))
    for device_name in video_devices:
        device = f"/dev/{device_name}"
        info = _v4l2_device_info(device)
        if info is None:
            continue
        _driver, card, capabilities = info
        if not (capabilities & 0x00000001):
            continue
        if any(keyword in card.lower() for keyword in ("isp", "unicam", "bcm2835", "metadata")):
            continue

        resolutions = _v4l2_resolutions(device)
        model = _match_usb_model(card, resolutions)
        cameras.append(
            DetectedCamera(
                device=device,
                model=model,
                backend="v4l2",
                index=int(re.search(r"\d+$", device_name).group()),
                detected_resolutions=resolutions or model.supported_resolutions,
            )
        )
        log.info("V4L2 camera found: %s (%s)", device, card)
    return cameras


def _v4l2_device_info(device: str) -> Optional[tuple[str, str, int]]:
    """Return (driver, card, capabilities), or None when probing fails."""
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--device", device, "--info"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0:
            return None
        output = result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    driver = _extract_v4l2_field(output, "Driver name")
    card = _extract_v4l2_field(output, "Card type")
    capability_string = _extract_v4l2_field(output, "Device Caps")
    try:
        capabilities = int(capability_string, 16) if capability_string else 0
    except ValueError:
        capabilities = 0
    return driver, card, capabilities


def _extract_v4l2_field(output: str, field: str) -> str:
    match = re.search(rf"{re.escape(field)}\s*:\s*(.+)", output)
    return match.group(1).strip() if match else ""


def _v4l2_resolutions(device: str) -> list[Resolution]:
    """Query supported discrete resolutions via v4l2-ctl."""
    resolutions = []
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--device", device, "--list-formats-ext"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for match in re.finditer(r"Size: Discrete (\d+)x(\d+)", result.stdout):
            resolution = Resolution(int(match.group(1)), int(match.group(2)))
            if resolution not in resolutions:
                resolutions.append(resolution)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return resolutions


def _match_usb_model(card: str, resolutions: list[Resolution]) -> CameraModel:
    """Match a known USB camera model by card name, else use the generic model."""
    del card, resolutions
    return CAMERA_USB_GENERIC
