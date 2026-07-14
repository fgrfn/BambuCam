"""
Configuration management for BambuCam.

Config is loaded from (in order, later overrides earlier):
  1. Built-in defaults
  2. /etc/bambucam/bambucam.yaml  (system-wide)
  3. ~/.config/bambucam/bambucam.yaml  (user)
  4. BAMBUCAM_CONFIG env var path
  5. --config CLI argument

All settings are also writable at runtime via the WebUI/API and persisted
back to the selected writable config file.
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml

log = logging.getLogger(__name__)


DEFAULTS: dict = {
    "camera": {
        "index": 0,
        "backend": "auto",
        "module": "auto",
        # "auto" lets startup select a model- and hardware-aware mode. Existing
        # installations with explicit values remain unchanged.
        "resolution": "auto",
        "framerate": "auto",
        "brightness": 0.0,
        "contrast": 1.0,
        "saturation": 1.0,
        "sharpness": 1.0,
        "exposure_mode": "auto",
        "awb_mode": "auto",
        "vflip": False,
        "hflip": False,
        "autofocus": True,
        "hdr": False,
    },
    "streaming": {
        "mjpeg": {
            "enabled": True,
            "port": 8080,
            "path": "/stream",
            "quality": 85,
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
        "secret_key": "",
        "auth": {
            "enabled": False,
            "username": "admin",
            "password": "",
            "api_token": "",
        },
        "trust_proxy": False,
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
    """Runtime configuration container with atomic persistence."""

    def __init__(self):
        self._data: dict = {}
        self._user_config_path: Optional[Path] = None

    def load(self, config_path: Optional[Path] = None) -> None:
        """Load config from all sources and merge them in precedence order."""
        result = _deep_merge({}, DEFAULTS)

        for path in [_SYSTEM_CONFIG, _USER_CONFIG]:
            if path.exists():
                log.debug("Loading config from %s", path)
                result = _deep_merge(result, _load_yaml(path))

        env_path = os.environ.get("BAMBUCAM_CONFIG")
        if env_path:
            path = Path(env_path)
            if path.exists():
                result = _deep_merge(result, _load_yaml(path))

        if config_path and config_path.exists():
            result = _deep_merge(result, _load_yaml(config_path))
            self._user_config_path = config_path
        elif _SYSTEM_CONFIG.exists():
            # The service owns this file and should persist WebUI changes there.
            self._user_config_path = _SYSTEM_CONFIG
        else:
            self._user_config_path = _USER_CONFIG

        self._data = result
        log.info("Configuration loaded (will persist to %s)", self._user_config_path)

    def save(self) -> None:
        """Persist current settings atomically to avoid truncated YAML files."""
        path = self._user_config_path or _USER_CONFIG
        path.parent.mkdir(parents=True, exist_ok=True)
        rendered = yaml.safe_dump(self._data, default_flow_style=False, sort_keys=False)

        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
                tmp_path = Path(handle.name)

            if path.exists():
                tmp_path.chmod(path.stat().st_mode & 0o777)
            else:
                tmp_path.chmod(0o640)
            os.replace(tmp_path, path)
            log.debug("Config saved atomically to %s", path)
        finally:
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def get(self, *keys: str, default: Any = None) -> Any:
        """Get a nested config value by path components."""
        node: Any = self._data
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set(self, *keys: str, value: Any) -> None:
        """Set a nested config value in memory."""
        if not keys:
            raise ValueError("At least one config key is required")
        node = self._data
        for key in keys[:-1]:
            child = node.setdefault(key, {})
            if not isinstance(child, dict):
                raise ValueError(f"Config path component {key!r} is not a section")
            node = child
        node[keys[-1]] = value

    def update_section(self, section: str, values: dict) -> None:
        """Merge a dictionary into one top-level section."""
        if not isinstance(values, dict):
            raise TypeError("Section update must be a dictionary")
        current = self._data.setdefault(section, {})
        if not isinstance(current, dict):
            raise ValueError(f"Config section {section!r} is not a mapping")
        self._data[section] = _deep_merge(current, values)

    def as_dict(self) -> dict:
        return _deep_copy(self._data)

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


def _load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log.warning("Failed to load config from %s: %s", path, exc)
        return {}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _deep_copy(data: dict) -> dict:
    import copy

    return copy.deepcopy(data)


_config: Optional[Config] = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
        _config.load()
    return _config
