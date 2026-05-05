"""Camera model definitions and capability matrices."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Resolution:
    width: int
    height: int

    def __str__(self) -> str:
        return f"{self.width}x{self.height}"

    def as_tuple(self) -> tuple:
        return (self.width, self.height)

    @classmethod
    def from_string(cls, s: str) -> "Resolution":
        parts = s.lower().replace(" ", "").split("x")
        if len(parts) != 2:
            raise ValueError(f"Invalid resolution string: {s!r}")
        return cls(int(parts[0]), int(parts[1]))


@dataclass
class CameraModel:
    """Describes a specific camera module and its hardware capabilities."""

    id: str
    name: str
    sensor: str
    megapixels: float
    max_resolution: Resolution
    max_framerate: int
    supported_resolutions: list[Resolution]
    supported_framerates: list[int]

    # Per-resolution max framerate (Resolution → max fps for that mode).
    # The UI uses this to filter the framerate dropdown so invalid combos
    # (e.g. 90 fps @ 2592x1944 on OV5647) are never shown.
    resolution_max_framerates: dict = field(default_factory=dict)

    # Feature flags
    has_autofocus: bool = False
    has_hdr: bool = False
    has_noise_reduction: bool = False
    is_noir: bool = False  # No IR filter
    has_global_shutter: bool = False

    # Sensor-specific tuning
    vflip_default: bool = False
    hflip_default: bool = False

    description: str = ""
    libcamera_name: Optional[str] = None  # name reported by libcamera-hello

    def supports_resolution(self, res: Resolution) -> bool:
        return res in self.supported_resolutions

    def best_framerate_for(self, desired: int) -> int:
        """Return the highest supported framerate <= desired."""
        candidates = [f for f in self.supported_framerates if f <= desired]
        return max(candidates) if candidates else self.supported_framerates[0]


# ---------------------------------------------------------------------------
# Known Raspberry Pi camera modules
# ---------------------------------------------------------------------------

_RES = Resolution  # shorthand

CAMERA_V1 = CameraModel(
    id="ov5647_v1",
    name="Camera Module v1",
    sensor="OV5647",
    megapixels=5.0,
    max_resolution=_RES(2592, 1944),
    max_framerate=90,
    supported_resolutions=[
        _RES(2592, 1944),
        _RES(1920, 1080),
        _RES(1296, 972),
        _RES(1296, 730),
        _RES(640, 480),
    ],
    supported_framerates=[90, 60, 30, 25, 15, 10, 5],
    resolution_max_framerates={
        _RES(2592, 1944): 15,
        _RES(1920, 1080): 30,
        _RES(1296, 972): 42,
        _RES(1296, 730): 49,
        _RES(640, 480): 90,
    },
    description="Original Raspberry Pi Camera Module (OV5647, 5MP)",
    libcamera_name="ov5647",
)

CAMERA_V2 = CameraModel(
    id="imx219_v2",
    name="Camera Module v2",
    sensor="IMX219",
    megapixels=8.0,
    max_resolution=_RES(3280, 2464),
    max_framerate=90,
    supported_resolutions=[
        _RES(3280, 2464),
        _RES(1920, 1080),
        _RES(1640, 1232),
        _RES(1640, 922),
        _RES(1280, 720),
        _RES(640, 480),
    ],
    supported_framerates=[90, 60, 30, 25, 15, 10, 5],
    resolution_max_framerates={
        _RES(3280, 2464): 15,
        _RES(1920, 1080): 30,
        _RES(1640, 1232): 41,
        _RES(1640, 922): 50,
        _RES(1280, 720): 90,
        _RES(640, 480): 90,
    },
    description="Raspberry Pi Camera Module v2 (IMX219, 8MP)",
    libcamera_name="imx219",
)

CAMERA_V2_NOIR = CameraModel(
    id="imx219_v2_noir",
    name="Camera Module v2 NoIR",
    sensor="IMX219",
    megapixels=8.0,
    max_resolution=_RES(3280, 2464),
    max_framerate=90,
    supported_resolutions=CAMERA_V2.supported_resolutions,
    supported_framerates=CAMERA_V2.supported_framerates,
    is_noir=True,
    description="Raspberry Pi Camera Module v2 without IR filter (IMX219, 8MP)",
    libcamera_name="imx219",
)

CAMERA_V3 = CameraModel(
    id="imx708_v3",
    name="Camera Module 3",
    sensor="IMX708",
    megapixels=11.9,
    max_resolution=_RES(4608, 2592),
    max_framerate=120,
    supported_resolutions=[
        _RES(4608, 2592),
        _RES(2304, 1296),
        _RES(1920, 1080),
        _RES(1536, 864),
        _RES(1280, 720),
        _RES(640, 480),
    ],
    supported_framerates=[120, 60, 30, 25, 15, 10, 5],
    resolution_max_framerates={
        _RES(4608, 2592): 14,
        _RES(2304, 1296): 56,
        _RES(1920, 1080): 56,
        _RES(1536, 864): 120,
        _RES(1280, 720): 120,
        _RES(640, 480): 120,
    },
    has_autofocus=True,
    has_hdr=True,
    description="Raspberry Pi Camera Module 3 (IMX708, 12MP, autofocus, HDR)",
    libcamera_name="imx708",
)

CAMERA_V3_WIDE = CameraModel(
    id="imx708_v3_wide",
    name="Camera Module 3 Wide",
    sensor="IMX708",
    megapixels=11.9,
    max_resolution=_RES(4608, 2592),
    max_framerate=120,
    supported_resolutions=CAMERA_V3.supported_resolutions,
    supported_framerates=CAMERA_V3.supported_framerates,
    has_autofocus=True,
    has_hdr=True,
    description="Raspberry Pi Camera Module 3 Wide angle (IMX708, 12MP)",
    libcamera_name="imx708",
)

CAMERA_V3_NOIR = CameraModel(
    id="imx708_v3_noir",
    name="Camera Module 3 NoIR",
    sensor="IMX708",
    megapixels=11.9,
    max_resolution=_RES(4608, 2592),
    max_framerate=120,
    supported_resolutions=CAMERA_V3.supported_resolutions,
    supported_framerates=CAMERA_V3.supported_framerates,
    has_autofocus=True,
    has_hdr=True,
    is_noir=True,
    description="Raspberry Pi Camera Module 3 without IR filter (IMX708, 12MP)",
    libcamera_name="imx708",
)

CAMERA_V3_WIDE_NOIR = CameraModel(
    id="imx708_v3_wide_noir",
    name="Camera Module 3 Wide NoIR",
    sensor="IMX708",
    megapixels=11.9,
    max_resolution=_RES(4608, 2592),
    max_framerate=120,
    supported_resolutions=CAMERA_V3.supported_resolutions,
    supported_framerates=CAMERA_V3.supported_framerates,
    has_autofocus=True,
    has_hdr=True,
    is_noir=True,
    description="Raspberry Pi Camera Module 3 Wide without IR filter (IMX708, 12MP)",
    libcamera_name="imx708",
)

CAMERA_HQ = CameraModel(
    id="imx477_hq",
    name="HQ Camera",
    sensor="IMX477",
    megapixels=12.3,
    max_resolution=_RES(4056, 3040),
    max_framerate=60,
    supported_resolutions=[
        _RES(4056, 3040),
        _RES(2028, 1520),
        _RES(2028, 1080),
        _RES(1920, 1080),
        _RES(1280, 720),
        _RES(640, 480),
    ],
    supported_framerates=[60, 30, 25, 15, 10, 5],
    resolution_max_framerates={
        _RES(4056, 3040): 10,
        _RES(2028, 1520): 40,
        _RES(2028, 1080): 50,
        _RES(1920, 1080): 50,
        _RES(1280, 720): 60,
        _RES(640, 480): 60,
    },
    description="Raspberry Pi HQ Camera (IMX477, 12.3MP, interchangeable lens)",
    libcamera_name="imx477",
)

CAMERA_GS = CameraModel(
    id="imx296_gs",
    name="Global Shutter Camera",
    sensor="IMX296",
    megapixels=1.6,
    max_resolution=_RES(1456, 1088),
    max_framerate=60,
    supported_resolutions=[
        _RES(1456, 1088),
        _RES(1280, 720),
        _RES(720, 540),
        _RES(640, 480),
    ],
    supported_framerates=[60, 30, 25, 15, 10],
    resolution_max_framerates={
        _RES(1456, 1088): 60,
        _RES(1280, 720): 60,
        _RES(720, 540): 60,
        _RES(640, 480): 60,
    },
    has_global_shutter=True,
    description="Raspberry Pi Global Shutter Camera (IMX296, 1.6MP)",
    libcamera_name="imx296",
)

CAMERA_USB_GENERIC = CameraModel(
    id="usb_generic",
    name="USB Webcam",
    sensor="Unknown",
    megapixels=0.0,
    max_resolution=_RES(1920, 1080),
    max_framerate=30,
    supported_resolutions=[
        _RES(1920, 1080),
        _RES(1280, 720),
        _RES(1024, 768),
        _RES(800, 600),
        _RES(640, 480),
        _RES(320, 240),
    ],
    supported_framerates=[30, 25, 20, 15, 10, 5],
    description="Generic USB webcam (V4L2)",
)

# ---------------------------------------------------------------------------
# Registry: libcamera sensor name → model
# ---------------------------------------------------------------------------

KNOWN_MODELS: list[CameraModel] = [
    CAMERA_V1,
    CAMERA_V2,
    CAMERA_V2_NOIR,
    CAMERA_V3,
    CAMERA_V3_WIDE,
    CAMERA_V3_NOIR,
    CAMERA_V3_WIDE_NOIR,
    CAMERA_HQ,
    CAMERA_GS,
    CAMERA_USB_GENERIC,
]

LIBCAMERA_MODEL_MAP: dict = {
    "ov5647": CAMERA_V1,
    "imx219": CAMERA_V2,
    "imx708": CAMERA_V3,
    "imx477": CAMERA_HQ,
    "imx296": CAMERA_GS,
}


def get_model_by_id(model_id: str) -> Optional[CameraModel]:
    for m in KNOWN_MODELS:
        if m.id == model_id:
            return m
    return None


def get_model_by_sensor(sensor_name: str) -> Optional[CameraModel]:
    return LIBCAMERA_MODEL_MAP.get(sensor_name.lower())


# Human-friendly aliases used in bambucam.yaml  camera.module
MODULE_ALIAS_MAP: dict = {
    "v1": CAMERA_V1,
    "v2": CAMERA_V2,
    "v2_noir": CAMERA_V2_NOIR,
    "v3": CAMERA_V3,
    "v3_noir": CAMERA_V3_NOIR,
    "v3_wide": CAMERA_V3_WIDE,
    "v3_wide_noir": CAMERA_V3_WIDE_NOIR,
    "hq": CAMERA_HQ,
    "gs": CAMERA_GS,
}


def get_model_by_alias(alias: str) -> Optional[CameraModel]:
    """Return model for a config alias (e.g. 'v3_noir'), or None for 'auto'."""
    if not alias or alias.lower() == "auto":
        return None
    return MODULE_ALIAS_MAP.get(alias.lower())
