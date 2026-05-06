"""Abstract base class for camera backends."""

import abc
from collections.abc import Iterator
from typing import Optional

from bambucam.camera.models import CameraModel, Resolution


class CameraBackend(abc.ABC):
    """Common interface every camera backend must implement."""

    def __init__(self, model: CameraModel, device: str):
        self.model = model
        self.device = device
        self._running = False

    @abc.abstractmethod
    def configure(
        self,
        resolution: Resolution,
        framerate: int,
        **kwargs,
    ) -> None:
        """Apply camera settings (resolution, framerate, etc.)."""

    @abc.abstractmethod
    def start(self) -> None:
        """Start capture."""

    @abc.abstractmethod
    def stop(self) -> None:
        """Stop capture and release resources."""

    @abc.abstractmethod
    def capture_jpeg(self) -> bytes:
        """Return a single JPEG frame."""

    def frame_iterator(self) -> Iterator[bytes]:
        """Yield JPEG frames continuously until stopped."""
        while self._running:
            yield self.capture_jpeg()

    @property
    def is_running(self) -> bool:
        return self._running

    # Optional overrides
    def set_brightness(self, value: float) -> None: ...
    def set_contrast(self, value: float) -> None: ...
    def set_saturation(self, value: float) -> None: ...
    def set_sharpness(self, value: float) -> None: ...
    def set_exposure_mode(self, mode: str) -> None: ...
    def set_awb_mode(self, mode: str) -> None: ...
    def set_iso(self, iso: int) -> None: ...
    def set_vflip(self, enabled: bool) -> None: ...
    def set_hflip(self, enabled: bool) -> None: ...
    def set_autofocus(self, enabled: bool) -> None: ...
    def set_hdr(self, enabled: bool) -> None: ...
    def set_noise_reduction(self, mode: str) -> None: ...
    def get_v4l2_device(self) -> Optional[str]:
        """Return V4L2 device path for ffmpeg RTSP pipeline, if applicable."""
        return None
