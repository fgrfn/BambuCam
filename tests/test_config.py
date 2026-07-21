"""Tests for configuration management."""

import tempfile
from pathlib import Path

import pytest
import yaml

from bambucam.config import (
    CURRENT_CONFIG_VERSION,
    DEFAULTS,
    Config,
    _deep_merge,
    validate_config_update,
)


class TestDeepMerge:
    def test_simple_override(self):
        result = _deep_merge({"a": 1, "b": 2}, {"b": 99})
        assert result == {"a": 1, "b": 99}

    def test_nested_merge(self):
        base = {"camera": {"fps": 15, "res": "1080p"}}
        over = {"camera": {"fps": 30}}
        result = _deep_merge(base, over)
        assert result["camera"]["fps"] == 30
        assert result["camera"]["res"] == "1080p"

    def test_new_keys_added(self):
        result = _deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_nested_values_do_not_alias_the_source(self):
        source = {"section": {"value": 1}}
        result = _deep_merge({}, source)
        result["section"]["value"] = 2
        assert source["section"]["value"] == 1


class TestConfig:
    def test_defaults_loaded(self):
        from unittest.mock import patch

        cfg = Config()
        # Patch away system/user config paths so only pure defaults are loaded
        with (
            patch("bambucam.config._SYSTEM_CONFIG", Path("/nonexistent/sys.yaml")),
            patch("bambucam.config._USER_CONFIG", Path("/nonexistent/user.yaml")),
        ):
            cfg.load(Path("/nonexistent/bambucam.yaml"))
        assert cfg.get("camera", "framerate") == DEFAULTS["camera"]["framerate"]

    def test_get_nested(self):
        cfg = Config()
        cfg.load()
        assert cfg.get("streaming", "mjpeg", "enabled") is True

    def test_get_missing_returns_default(self):
        cfg = Config()
        cfg.load()
        assert cfg.get("nonexistent", "key", default="fallback") == "fallback"

    def test_set_value(self):
        cfg = Config()
        cfg.load()
        cfg.set("camera", "framerate", value=30)
        assert cfg.get("camera", "framerate") == 30

    def test_load_yaml_override(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            yaml.safe_dump({"camera": {"framerate": 60}}, f)
            path = Path(f.name)

        cfg = Config()
        cfg.load(path)
        assert cfg.get("camera", "framerate") == 60
        # Other defaults still present
        assert cfg.get("streaming", "mjpeg", "enabled") is True
        path.unlink()

    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.yaml"
            cfg = Config()
            cfg.load()
            cfg._user_config_path = path
            cfg.set("camera", "framerate", value=60)
            cfg.save()

            cfg2 = Config()
            cfg2.load(path)
            assert cfg2.get("camera", "framerate") == 60

    def test_update_section(self):
        cfg = Config()
        cfg.load()
        cfg.update_section("camera", {"framerate": 25, "brightness": 0.5})
        assert cfg.get("camera", "framerate") == 25
        assert cfg.get("camera", "brightness") == 0.5

    def test_camera_property(self):
        cfg = Config()
        cfg.load()
        assert isinstance(cfg.camera, dict)
        assert "framerate" in cfg.camera

    def test_as_dict_is_copy(self):
        cfg = Config()
        cfg.load()
        d = cfg.as_dict()
        d["camera"]["framerate"] = 999
        assert cfg.get("camera", "framerate") != 999

    def test_legacy_file_is_migrated_without_changing_explicit_rtsp_choice(self, tmp_path):
        path = tmp_path / "legacy.yaml"
        path.write_text("streaming:\n  rtsp:\n    enabled: true\n", encoding="utf-8")

        cfg = Config()
        cfg.load(path)

        assert cfg.get("streaming", "rtsp", "enabled") is True
        assert cfg.get("system", "config_version") == CURRENT_CONFIG_VERSION
        persisted = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert persisted["system"]["config_version"] == CURRENT_CONFIG_VERSION

    def test_future_config_version_is_rejected(self, tmp_path):
        path = tmp_path / "future.yaml"
        path.write_text("system:\n  config_version: 999\n", encoding="utf-8")

        with pytest.raises(ValueError, match="newer than supported"):
            Config().load(path)


def test_schema_rejects_unknown_nested_settings():
    with pytest.raises(ValueError, match="Unknown camera setting"):
        validate_config_update({"camera": {"turbo_mode": True}})


def test_schema_rejects_port_conflicts():
    with pytest.raises(ValueError, match="Port conflict"):
        validate_config_update({"streaming": {"rtsp": {"port": 8080}}})


def test_schema_rejects_zoom_outside_supported_range():
    with pytest.raises(ValueError, match="camera.zoom must be between 1.0 and 8.0"):
        validate_config_update({"camera": {"zoom": 8.1}})


def test_fresh_install_uses_hardware_aware_rtsp_default():
    assert DEFAULTS["streaming"]["rtsp"]["enabled"] == "auto"
