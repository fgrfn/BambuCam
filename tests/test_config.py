"""Tests for configuration management."""

import tempfile
from pathlib import Path

import yaml

from bambucam.config import DEFAULTS, Config, _deep_merge


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
            cfg._user_config_path = path
            cfg.load()
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
