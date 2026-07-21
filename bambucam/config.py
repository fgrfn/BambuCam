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

import copy
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml

log = logging.getLogger(__name__)

CURRENT_CONFIG_VERSION = 1


DEFAULTS: dict = {
    "camera": {
        "index": 0,
        "backend": "auto",
        "module": "auto",
        "active_profile": "custom",
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
        "noise_reduction": "fast",
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
            # Fresh installations follow the detected hardware tier. Existing
            # explicit true/false values are retained by configuration migration.
            "enabled": "auto",
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
            "max_count": 500,
            "max_age_days": 30,
            "max_bytes": 1073741824,
        },
        "timelapse": {
            "enabled": True,
            "save_dir": "/var/lib/bambucam/timelapse",
            "interval_seconds": 10,
            "output_fps": 30,
            "render_on_stop": True,
            "max_sessions": 20,
            "max_age_days": 90,
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
        "config_version": CURRENT_CONFIG_VERSION,
        "log_level": "INFO",
        "mediamtx_path": "/usr/local/bin/mediamtx",
        "ffmpeg_path": "ffmpeg",
        "diagnostics_log_lines": 300,
        "update_include_prerelease": False,
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
        migration_needed = False

        for path in [_SYSTEM_CONFIG, _USER_CONFIG]:
            if path.exists():
                log.debug("Loading config from %s", path)
                loaded = _load_yaml(path)
                migration_needed |= _source_needs_migration(loaded)
                result = _deep_merge(result, loaded)

        env_path = os.environ.get("BAMBUCAM_CONFIG")
        if env_path:
            path = Path(env_path)
            if path.exists():
                loaded = _load_yaml(path)
                migration_needed |= _source_needs_migration(loaded)
                result = _deep_merge(result, loaded)

        if config_path and config_path.exists():
            loaded = _load_yaml(config_path)
            migration_needed |= _source_needs_migration(loaded)
            result = _deep_merge(result, loaded)
            self._user_config_path = config_path
        elif _SYSTEM_CONFIG.exists():
            # The service owns this file and should persist WebUI changes there.
            self._user_config_path = _SYSTEM_CONFIG
        else:
            self._user_config_path = _USER_CONFIG

        if (
            migration_needed
            and isinstance(result.get("system"), dict)
            and result["system"].get("config_version") == CURRENT_CONFIG_VERSION
        ):
            result["system"]["config_version"] = 0
        result, migrated = migrate_config(result)
        migrated = migrated or migration_needed
        validate_config(result)
        self._data = result
        log.info("Configuration loaded (will persist to %s)", self._user_config_path)
        if migrated and self._user_config_path and self._user_config_path.exists():
            try:
                self.save()
            except OSError as exc:
                log.warning("Could not persist migrated configuration: %s", exc)

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

    def replace(self, data: dict) -> None:
        """Replace the in-memory state with a validated deep copy."""
        validate_config(data)
        self._data = _deep_copy(data)

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
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _deep_copy(data: dict) -> dict:
    return copy.deepcopy(data)


def migrate_config(data: dict) -> tuple[dict, bool]:
    """Upgrade older configuration shapes without changing explicit user choices."""
    migrated = _deep_copy(data)
    system = migrated.setdefault("system", {})
    if not isinstance(system, dict):
        raise ValueError("system must be an object")
    raw_version = system.get("config_version", 0)
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise ValueError("system.config_version must be an integer") from exc
    if version > CURRENT_CONFIG_VERSION:
        raise ValueError(
            f"Configuration version {version} is newer than supported version "
            f"{CURRENT_CONFIG_VERSION}"
        )

    changed = version != CURRENT_CONFIG_VERSION
    if version < 1:
        # MJPEG is served by the WebUI. The old separate port was never an
        # independent listener, so migrate it to the authoritative WebUI port.
        web = migrated.get("web", {})
        mjpeg = migrated.get("streaming", {}).get("mjpeg", {})
        if isinstance(web, dict) and isinstance(mjpeg, dict) and "port" in web:
            mjpeg["port"] = web["port"]

    # Version 1 records the schema version. Explicit stream choices, including
    # an enabled RTSP stream, intentionally remain untouched.
    system["config_version"] = CURRENT_CONFIG_VERSION
    return migrated, changed


def _source_needs_migration(data: dict) -> bool:
    system = data.get("system", {})
    return not isinstance(system, dict) or system.get("config_version") != CURRENT_CONFIG_VERSION


def validate_config_update(update: dict, current: Optional[dict] = None) -> dict:
    """Validate a partial update and return the resulting complete configuration."""
    if not isinstance(update, dict):
        raise ValueError("Configuration must be an object")
    unknown = set(update) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown config section(s): {', '.join(sorted(unknown))}")
    if any(not isinstance(value, dict) for value in update.values()):
        raise ValueError("Every config section must be an object")
    merged = _deep_merge(current if current is not None else DEFAULTS, update)
    validate_config(merged)
    return merged


def validate_config(data: dict) -> None:
    """Validate the complete persisted schema, including cross-field constraints."""
    if not isinstance(data, dict):
        raise ValueError("Configuration must be an object")
    _reject_unknown(data, DEFAULTS, "configuration")

    camera = _mapping(data, "camera")
    _reject_unknown(camera, DEFAULTS["camera"], "camera")
    _integer(camera.get("index"), "camera.index", 0, 32)
    _choice(camera.get("backend"), "camera.backend", {"auto", "picamera2", "v4l2"})
    _string(camera.get("module"), "camera.module", allow_empty=False)
    _choice(
        camera.get("active_profile"),
        "camera.active_profile",
        {"custom", "quality", "balanced", "low_latency", "low_power"},
    )
    resolution = camera.get("resolution")
    if not (
        isinstance(resolution, str)
        and (resolution.strip().lower() == "auto" or re.fullmatch(r"[1-9]\d*x[1-9]\d*", resolution))
    ):
        raise ValueError("camera.resolution must be 'auto' or WIDTHxHEIGHT")
    framerate = camera.get("framerate")
    if not (isinstance(framerate, str) and framerate.strip().lower() == "auto"):
        _integer(framerate, "camera.framerate", 1, 120)
    _number(camera.get("brightness"), "camera.brightness", -1.0, 1.0)
    for key in ("contrast", "saturation", "sharpness"):
        _number(camera.get(key), f"camera.{key}", 0.0, 32.0)
    _choice(camera.get("exposure_mode"), "camera.exposure_mode", {"auto", "sport", "night"})
    _choice(
        camera.get("awb_mode"),
        "camera.awb_mode",
        {"auto", "sunlight", "cloudy", "shade", "tungsten", "fluorescent", "indoor"},
    )
    _choice(
        camera.get("noise_reduction"),
        "camera.noise_reduction",
        {"off", "minimal", "fast", "high_quality"},
    )
    for key in ("vflip", "hflip", "autofocus", "hdr"):
        _boolean(camera.get(key), f"camera.{key}")

    streaming = _mapping(data, "streaming")
    _reject_unknown(streaming, DEFAULTS["streaming"], "streaming")
    mjpeg = _mapping(streaming, "mjpeg")
    _reject_unknown(mjpeg, DEFAULTS["streaming"]["mjpeg"], "streaming.mjpeg")
    _boolean(mjpeg.get("enabled"), "streaming.mjpeg.enabled")
    _integer(mjpeg.get("port"), "streaming.mjpeg.port", 1, 65535)
    _http_path(mjpeg.get("path"), "streaming.mjpeg.path")
    _integer(mjpeg.get("quality"), "streaming.mjpeg.quality", 1, 100)
    _integer(mjpeg.get("fps"), "streaming.mjpeg.fps", 1, 120)

    rtsp = _mapping(streaming, "rtsp")
    _reject_unknown(rtsp, DEFAULTS["streaming"]["rtsp"], "streaming.rtsp")
    _boolean_or_auto(rtsp.get("enabled"), "streaming.rtsp.enabled")
    for key in ("port", "hls_port", "webrtc_port"):
        _integer(rtsp.get(key), f"streaming.rtsp.{key}", 1, 65535)
    _integer(rtsp.get("bitrate_kbps"), "streaming.rtsp.bitrate_kbps", 100, 100000)
    stream_name = _string(rtsp.get("stream_name"), "streaming.rtsp.stream_name")
    if any(char in stream_name for char in " /?#"):
        raise ValueError("Invalid RTSP stream name")
    for key in ("enable_hls", "enable_webrtc"):
        _boolean(rtsp.get(key), f"streaming.rtsp.{key}")
    rtsp_auth = _mapping(rtsp, "auth")
    _reject_unknown(rtsp_auth, DEFAULTS["streaming"]["rtsp"]["auth"], "streaming.rtsp.auth")
    _boolean(rtsp_auth.get("enabled"), "streaming.rtsp.auth.enabled")
    _string(rtsp_auth.get("username"), "streaming.rtsp.auth.username", allow_empty=True)
    _string(rtsp_auth.get("password"), "streaming.rtsp.auth.password", allow_empty=True)
    if rtsp_auth.get("enabled") and not (rtsp_auth.get("username") and rtsp_auth.get("password")):
        raise ValueError("RTSP authentication requires username and password")

    snapshot = _mapping(streaming, "snapshot")
    _reject_unknown(snapshot, DEFAULTS["streaming"]["snapshot"], "streaming.snapshot")
    _boolean(snapshot.get("enabled"), "streaming.snapshot.enabled")
    _http_path(snapshot.get("path"), "streaming.snapshot.path")
    _string(snapshot.get("save_dir"), "streaming.snapshot.save_dir")
    _integer(snapshot.get("max_count"), "streaming.snapshot.max_count", 0, 1000000)
    _integer(snapshot.get("max_age_days"), "streaming.snapshot.max_age_days", 0, 36500)
    _integer(snapshot.get("max_bytes"), "streaming.snapshot.max_bytes", 0, 10**15)

    timelapse = _mapping(streaming, "timelapse")
    _reject_unknown(timelapse, DEFAULTS["streaming"]["timelapse"], "streaming.timelapse")
    _boolean(timelapse.get("enabled"), "streaming.timelapse.enabled")
    _string(timelapse.get("save_dir"), "streaming.timelapse.save_dir")
    _integer(timelapse.get("interval_seconds"), "streaming.timelapse.interval_seconds", 1, 86400)
    _integer(timelapse.get("output_fps"), "streaming.timelapse.output_fps", 1, 120)
    _boolean(timelapse.get("render_on_stop"), "streaming.timelapse.render_on_stop")
    _integer(timelapse.get("max_sessions"), "streaming.timelapse.max_sessions", 0, 100000)
    _integer(timelapse.get("max_age_days"), "streaming.timelapse.max_age_days", 0, 36500)

    web = _mapping(data, "web")
    _reject_unknown(web, DEFAULTS["web"], "web")
    _string(web.get("host"), "web.host")
    _integer(web.get("port"), "web.port", 1, 65535)
    _string(web.get("secret_key"), "web.secret_key", allow_empty=True)
    _boolean(web.get("trust_proxy"), "web.trust_proxy")
    web_auth = _mapping(web, "auth")
    _reject_unknown(web_auth, DEFAULTS["web"]["auth"], "web.auth")
    _boolean(web_auth.get("enabled"), "web.auth.enabled")
    for key in ("username", "password", "api_token"):
        _string(web_auth.get(key), f"web.auth.{key}", allow_empty=True)
    if web_auth.get("enabled") and not (web_auth.get("password") or web_auth.get("api_token")):
        raise ValueError("Authentication requires a password or API token")
    https = _mapping(web, "https")
    _reject_unknown(https, DEFAULTS["web"]["https"], "web.https")
    _boolean(https.get("enabled"), "web.https.enabled")
    _string(https.get("cert"), "web.https.cert", allow_empty=True)
    _string(https.get("key"), "web.https.key", allow_empty=True)
    if https.get("enabled") and not (https.get("cert") and https.get("key")):
        raise ValueError("HTTPS requires certificate and key paths")
    if int(mjpeg["port"]) != int(web["port"]):
        raise ValueError("streaming.mjpeg.port must match web.port")

    system = _mapping(data, "system")
    _reject_unknown(system, DEFAULTS["system"], "system")
    _integer(system.get("config_version"), "system.config_version", 1, CURRENT_CONFIG_VERSION)
    _choice(system.get("log_level"), "system.log_level", {"DEBUG", "INFO", "WARNING", "ERROR"})
    _string(system.get("mediamtx_path"), "system.mediamtx_path")
    _string(system.get("ffmpeg_path"), "system.ffmpeg_path")
    _integer(system.get("diagnostics_log_lines"), "system.diagnostics_log_lines", 0, 10000)
    _boolean(system.get("update_include_prerelease"), "system.update_include_prerelease")

    ports = [("web", int(web["port"])), ("rtsp", int(rtsp["port"]))]
    if rtsp.get("enable_hls"):
        ports.append(("hls", int(rtsp["hls_port"])))
    if rtsp.get("enable_webrtc"):
        ports.append(("webrtc", int(rtsp["webrtc_port"])))
    seen: dict[int, str] = {}
    for name, port in ports:
        if port in seen:
            raise ValueError(f"Port conflict: {seen[port]} and {name} both use {port}")
        seen[port] = name


def _mapping(parent: dict, key: str) -> dict:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _reject_unknown(value: dict, allowed: dict, name: str) -> None:
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"Unknown {name} setting(s): {', '.join(sorted(unknown))}")


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed != value or not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _boolean(value: Any, name: str) -> None:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")


def _boolean_or_auto(value: Any, name: str) -> None:
    if isinstance(value, bool):
        return
    if not (isinstance(value, str) and value.lower() == "auto"):
        raise ValueError(f"{name} must be true, false, or 'auto'")


def _choice(value: Any, name: str, choices: set[str]) -> None:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{name} must be one of: {', '.join(sorted(choices))}")


def _string(value: Any, name: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"{name} must be a string{'' if allow_empty else ' and not empty'}")
    return value


def _http_path(value: Any, name: str) -> None:
    path = _string(value, name)
    if not path.startswith("/") or ".." in path:
        raise ValueError(f"{name} must be an absolute URL path")


_config: Optional[Config] = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
