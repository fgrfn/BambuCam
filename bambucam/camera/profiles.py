"""Model-aware camera and streaming profiles."""

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional

from bambucam.camera.models import Resolution

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CameraProfile:
    """A user-facing preset resolved against the active camera model."""

    name: str
    label: str
    description: str
    target_resolution: Optional[Resolution]
    resolution_strategy: str
    target_fps: int
    mjpeg_quality: int
    bitrate_kbps: int
    image_settings: dict = field(default_factory=dict)


PROFILES: dict[str, CameraProfile] = {
    "quality": CameraProfile(
        name="quality",
        label="Maximale Qualität",
        description="Highest detected resolution with conservative frame rate and bitrate.",
        target_resolution=None,
        resolution_strategy="largest",
        target_fps=15,
        mjpeg_quality=95,
        bitrate_kbps=6000,
        image_settings={"noise_reduction": "high_quality"},
    ),
    "balanced": CameraProfile(
        name="balanced",
        label="Ausgeglichen",
        description="1080p-oriented profile for monitoring and everyday use.",
        target_resolution=Resolution(1920, 1080),
        resolution_strategy="closest",
        target_fps=15,
        mjpeg_quality=85,
        bitrate_kbps=2500,
        image_settings={"noise_reduction": "fast"},
    ),
    "low_latency": CameraProfile(
        name="low_latency",
        label="Niedrige Latenz",
        description="720p-oriented profile with up to 30 FPS and short exposure.",
        target_resolution=Resolution(1280, 720),
        resolution_strategy="closest",
        target_fps=30,
        mjpeg_quality=78,
        bitrate_kbps=3000,
        image_settings={"exposure_mode": "sport", "noise_reduction": "minimal"},
    ),
    "low_power": CameraProfile(
        name="low_power",
        label="Stromsparend",
        description="Smallest detected mode, low frame rate, and reduced encoding load.",
        target_resolution=None,
        resolution_strategy="smallest",
        target_fps=10,
        mjpeg_quality=70,
        bitrate_kbps=800,
        image_settings={"noise_reduction": "fast"},
    ),
}


class CameraProfileService:
    """Resolve and atomically persist camera/streaming presets."""

    def __init__(self, config, camera, mjpeg, rtsp):
        self._config = config
        self._camera = camera
        self._mjpeg = mjpeg
        self._rtsp = rtsp

    def list_profiles(self) -> list[dict]:
        active = self._config.get("camera", "active_profile", default="custom")
        return [
            {
                "name": profile.name,
                "label": profile.label,
                "description": profile.description,
                "active": profile.name == active,
                "resolved": self.resolve(profile.name),
            }
            for profile in PROFILES.values()
        ]

    def resolve(self, name: str) -> dict:
        profile = self._profile(name)
        model = self._camera.model
        if model is None:
            raise RuntimeError("No camera is selected")

        resolutions = self._available_resolutions()
        resolution = self._select_resolution(profile, resolutions)
        maximum_fps = model.resolution_max_framerates.get(
            resolution,
            model.max_framerate,
        )
        framerate = max(1, min(int(profile.target_fps), int(maximum_fps)))
        return {
            "name": profile.name,
            "label": profile.label,
            "description": profile.description,
            "resolution": str(resolution),
            "framerate": framerate,
            "mjpeg_quality": profile.mjpeg_quality,
            "bitrate_kbps": profile.bitrate_kbps,
            "image_settings": deepcopy(profile.image_settings),
        }

    def apply(self, name: str) -> dict:
        resolved = self.resolve(name)
        snapshot = self._config.as_dict()
        old_status = self._camera.status()
        camera_settings = {
            "resolution": resolved["resolution"],
            "framerate": resolved["framerate"],
            **resolved["image_settings"],
        }
        try:
            self._camera.apply_settings(camera_settings)
            self._camera.set_jpeg_quality(resolved["mjpeg_quality"])
            self._mjpeg.update_fps(resolved["framerate"])
            self._rtsp.update_settings(
                resolution=resolved["resolution"],
                framerate=resolved["framerate"],
                bitrate_kbps=resolved["bitrate_kbps"],
            )

            self._config.update_section(
                "camera",
                {
                    **camera_settings,
                    "active_profile": name,
                },
            )
            self._config.update_section(
                "streaming",
                {
                    "mjpeg": {
                        "quality": resolved["mjpeg_quality"],
                        "fps": resolved["framerate"],
                    },
                    "rtsp": {"bitrate_kbps": resolved["bitrate_kbps"]},
                },
            )
            self._config.save()
        except Exception:
            self._config.replace(snapshot)
            self._rollback_runtime(snapshot, old_status)
            raise
        log.info("Applied camera profile %s: %s", name, resolved)
        return resolved

    def _rollback_runtime(self, config: dict, old_status: dict) -> None:
        try:
            old_camera = config["camera"]
            camera_settings = {
                key: old_camera[key]
                for key in (
                    "brightness",
                    "contrast",
                    "saturation",
                    "sharpness",
                    "zoom",
                    "exposure_mode",
                    "awb_mode",
                    "noise_reduction",
                    "vflip",
                    "hflip",
                    "autofocus",
                    "hdr",
                )
                if key in old_camera
            }
            if old_status.get("resolution"):
                camera_settings["resolution"] = old_status["resolution"]
            if old_status.get("framerate"):
                camera_settings["framerate"] = old_status["framerate"]
            self._camera.apply_settings(camera_settings)
            mjpeg = config["streaming"]["mjpeg"]
            self._camera.set_jpeg_quality(int(mjpeg["quality"]))
            self._mjpeg.update_fps(int(mjpeg["fps"]))
            rtsp = config["streaming"]["rtsp"]
            self._rtsp.update_settings(
                resolution=old_status.get("resolution"),
                framerate=old_status.get("framerate"),
                bitrate_kbps=int(rtsp["bitrate_kbps"]),
            )
        except Exception:
            log.exception("Failed to roll back camera profile runtime state")

    def mark_custom(self) -> None:
        """Mark the active configuration as custom after a manual change."""
        if self._config.get("camera", "active_profile", default="custom") != "custom":
            self._config.set("camera", "active_profile", value="custom")
            self._config.save()

    def _available_resolutions(self) -> list[Resolution]:
        status = self._camera.status()
        values = status.get("available_resolutions") or []
        resolutions = []
        for value in values:
            try:
                resolution = Resolution.from_string(str(value))
            except (TypeError, ValueError):
                continue
            if resolution not in resolutions:
                resolutions.append(resolution)
        if not resolutions and self._camera.model is not None:
            resolutions = list(self._camera.model.supported_resolutions)
        if not resolutions:
            raise RuntimeError("The active camera did not report any resolutions")
        return resolutions

    @staticmethod
    def _select_resolution(
        profile: CameraProfile,
        resolutions: list[Resolution],
    ) -> Resolution:
        if profile.resolution_strategy == "largest":
            return max(resolutions, key=lambda item: item.width * item.height)
        if profile.resolution_strategy == "smallest":
            return min(resolutions, key=lambda item: item.width * item.height)
        if profile.target_resolution is None:
            raise RuntimeError(f"Profile {profile.name} has no target resolution")

        target = profile.target_resolution
        target_ratio = target.width / target.height

        def score(item: Resolution) -> tuple:
            ratio_error = abs(item.width / item.height - target_ratio)
            area_error = abs(item.width * item.height - target.width * target.height)
            return ratio_error, area_error

        return min(resolutions, key=score)

    @staticmethod
    def _profile(name: str) -> CameraProfile:
        try:
            return PROFILES[str(name)]
        except KeyError as exc:
            raise ValueError(
                f"Unknown camera profile {name!r}. Available: {', '.join(PROFILES)}"
            ) from exc
