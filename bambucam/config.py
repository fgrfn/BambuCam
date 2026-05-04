"""
Configuration management for BambuCam.

Config is loaded from (in order, later overrides earlier):
  1. Built-in defaults
  2. /etc/bambucam/bambucam.yaml  (system-wide)
  3. ~/.config/bambucam/bambucam.yaml  (user)
  4. BAMBUCAM_CONFIG env var path
  5. --config CLI argument

All settings are also writable at runtime via the WebUI/API and persisted
back to the user config file.
"""

import logging
import os
from pathlib import Path
from typing import Any, Optional

import yaml

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

DEFAULTS: dict = {
    "camera": {
        "index": 0,  # Which camera to use if multiple detected
        "backend": "auto",  # "auto" | "picamera2" | "v4l2"
        "resolution": "1920x1080",
        "framerate": 15,
        "brightness": 0.0,  # -1.0 … 1.0
        "contrast": 1.0,  # 0.0 … 32.0
        "saturation": 1.0,
        "sharpness": 1.0,
        "exposure_mode": "auto",  # auto | sport | night
        "awb_mode": "auto",  # auto | sunlight | cloudy | …
        "vflip": False,
        "hflip": False,
        "autofocus": True,  # if supported
        "hdr": False,  # if supported
    },
    "streaming": {
        "mjpeg": {
            "enabled": True,
            "port": 8080,
            "path": "/stream",
            "quality": 85,  # JPEG quality 1-100
            "fps": 15,
        },
        "rtsp": {
            "enabled": True,
            "port": 8554,
            "stream_name": "cam",
            "bitrate_kbps": 2000,
            "enable_hls": True,
            "hls_port": 8888,
            "enable_webrtc": False,
            "webrtc_port": 8889,
            "auth": {
                "enabled": False,
                "username": "",
                "password": "",
            },
        },
        "snapshot": {
            "enabled": True,
            "path": "/snapshot",
            "save_dir": "/var/lib/bambucam/snapshots",
        },
    },
    "web": {
        "host": "0.0.0.0",
        "port": 8080,
        "secret_key": "",  # Auto-generated on first start if empty
        "auth": {
            "enabled": False,
            "username": "admin",
            "password": "",
        },
        "https": {
            "enabled": False,
            "cert": "",
            "key": "",
        },
    },
    "system": {
        "log_level": "INFO",
        "mediamtx_path": "/usr/local/bin/mediamtx",
        "ffmpeg_path": "ffmpeg",
    },
}

_SYSTEM_CONFIG = Path("/etc/bambucam/bambucam.yaml")

try:
    _USER_CONFIG = Path.home() / ".config" / "bambucam" / "bambucam.yaml"
except RuntimeError:
    _USER_CONFIG = Path("/var/lib/bambucam/bambucam.yaml")


class Config:
    """Runtime configuration container with persistence."""

    def __init__(self):
        self._data: dict = {}
        self._user_config_path: Optional[Path] = None

    def load(self, config_path: Optional[Path] = None) -> None:
        """Load config from all sources and merge."""
        result = _deep_merge({}, DEFAULTS)

        for path in [_SYSTEM_CONFIG, _USER_CONFIG]:
            if path.exists():
                log.debug("Loading config from %s", path)
                result = _deep_merge(result, _load_yaml(path))

        env_path = os.environ.get("BAMBUCAM_CONFIG")
        if env_path:
            p = Path(env_path)
            if p.exists():
                result = _deep_merge(result, _load_yaml(p))

        if config_path and config_path.exists():
            result = _deep_merge(result, _load_yaml(config_path))
            self._user_config_path = config_path
        elif _SYSTEM_CONFIG.exists():
            # When running as the service, persist changes back to the system
            # config rather than ~/.config (home dir may not exist for the
            # service user).
            self._user_config_path = _SYSTEM_CONFIG
        else:
            self._user_config_path = _USER_CONFIG

        self._data = result
        log.info("Configuration loaded (will persist to %s)", self._user_config_path)

    def save(self) -> None:
        """Persist current settings to the user config file."""
        path = self._user_config_path or _USER_CONFIG
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(self._data, default_flow_style=False))
        log.debug("Config saved to %s", path)

    def get(self, *keys: str, default: Any = None) -> Any:
        """Get a nested config value by dot-path keys."""
        node = self._data
        for k in keys:
            if not isinstance(node, dict):
                return default
            node = node.get(k, default)
            if node is default:
                return default
        return node

    def set(self, *keys: str, value: Any) -> None:
        """Set a nested config value and persist."""
        node = self._data
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value

    def update_section(self, section: str, values: dict) -> None:
        """Merge a dict into a top-level section."""
        self._data.setdefault(section, {})
        self._data[section] = _deep_merge(self._data[section], values)

    def as_dict(self) -> dict:
        return _deep_copy(self._data)

    # Convenience accessors
    @property
    def camera(self) -> dict:
        return self._data.get("camera", {})

    @property
    def streaming(self) -> dict:
        return self._data.get("streaming", {})

    @property
    def web(self) -> dict:
        return self._data.get("web", {})

    @property
    def system(self) -> dict:
        return self._data.get("system", {})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log.warning("Failed to load config from %s: %s", path, e)
        return {}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _deep_copy(d: dict) -> dict:
    import copy

    return copy.deepcopy(d)


# Singleton
_config: Optional[Config] = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
        _config.load()
    return _config
