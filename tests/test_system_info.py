"""Tests for system_info helpers."""

from unittest.mock import patch

import pytest

from bambucam.system_info import pi_capability_tier, system_summary


@pytest.mark.parametrize(
    "model_string, expected_tier",
    [
        # Tier 1 — original Zero, Pi 1, Pi 2
        ("Raspberry Pi Zero Rev 1.3", 1),
        ("Raspberry Pi 1 Model B Rev 2", 1),
        ("Raspberry Pi 2 Model B Rev 1.1", 1),
        # Tier 2 — Zero 2 W (same SoC as Pi 3), Pi 3 variants
        ("Raspberry Pi Zero 2 W Rev 1.0", 2),
        ("Raspberry Pi 3 Model B Rev 1.2", 2),
        ("Raspberry Pi 3 Model B+ Rev 1.3", 2),
        ("Raspberry Pi 3 Model A+ Rev 1.0", 2),
        # Tier 3 — Pi 4, Pi 5, unknown
        ("Raspberry Pi 4 Model B Rev 1.4", 3),
        ("Raspberry Pi 5 Model B Rev 1.0", 3),
        ("Raspberry Pi 400 Rev 1.0", 3),
        # Compute Modules carry no "Pi <n>" and are classified by generation
        ("Raspberry Pi Compute Module Rev 1.1", 1),
        ("Raspberry Pi Compute Module 3 Rev 1.0", 2),
        ("Raspberry Pi Compute Module 3 Plus Rev 1.0", 2),
        ("Raspberry Pi Compute Module 4 Rev 1.1", 3),
        ("Raspberry Pi Compute Module 4S Rev 1.0", 3),
        ("Raspberry Pi Compute Module 5 Rev 1.0", 3),
        # Non-Pi hardware → assume capable
        (None, 3),
    ],
)
def test_pi_capability_tier(model_string, expected_tier):
    with patch("bambucam.system_info.raspberry_pi_model", return_value=model_string):
        assert pi_capability_tier() == expected_tier


def test_system_summary_reports_reboot_capability():
    with (
        patch("bambucam.system_control.system_reboot_available", return_value=True),
        patch("bambucam.system_info.cpu_usage", return_value=0.0),
    ):
        assert system_summary()["can_reboot"] is True


def test_throttle_status_decodes_current_and_latched_flags(tmp_path, monkeypatch):
    from bambucam import system_info

    sysfs = tmp_path / "get_throttled"
    sysfs.write_text("50005\n")
    monkeypatch.setattr(system_info, "_THROTTLED_PATH", sysfs)

    assert system_info.throttle_status() == {
        "raw": "0x50005",
        "active": ["under_voltage", "throttled"],
        "since_boot": ["under_voltage", "throttled"],
    }


def test_throttle_status_falls_back_to_vcgencmd(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from bambucam import system_info

    monkeypatch.setattr(system_info, "_THROTTLED_PATH", tmp_path / "missing")
    monkeypatch.setattr(
        system_info.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="throttled=0x80000\n"),
    )

    status = system_info.throttle_status()
    assert status["active"] == []
    assert status["since_boot"] == ["soft_temp_limit"]


def test_throttle_status_is_none_without_pi_firmware(tmp_path, monkeypatch):
    from bambucam import system_info

    def missing(*args, **kwargs):
        raise FileNotFoundError("vcgencmd")

    monkeypatch.setattr(system_info, "_THROTTLED_PATH", tmp_path / "missing")
    monkeypatch.setattr(system_info.subprocess, "run", missing)
    assert system_info.throttle_status() is None


def test_compute_module_camera_hint_only_on_compute_modules():
    from bambucam import system_info

    with patch.object(
        system_info, "raspberry_pi_model", return_value="Raspberry Pi Compute Module 4 Rev 1.1"
    ):
        hint = system_info.compute_module_camera_hint()
    assert hint["lines"] == ["camera_auto_detect=0", "dtoverlay=<sensor>"]
    assert hint["cam0_line"] == "dtoverlay=<sensor>,cam0"
    assert "camera_auto_detect=0" in system_info.compute_module_camera_hint_text(hint)

    with patch.object(system_info, "raspberry_pi_model", return_value="Raspberry Pi 4 Model B"):
        assert system_info.compute_module_camera_hint() is None


def test_missing_camera_error_mentions_overlay_on_compute_module():
    from bambucam.camera.manager import CameraManager

    manager = CameraManager()
    with (
        patch.object(manager, "detected_cameras", return_value=[]),
        patch(
            "bambucam.camera.manager.compute_module_camera_hint",
            return_value={
                "board": "Raspberry Pi Compute Module 4",
                "config_file": "/boot/firmware/config.txt",
                "lines": ["camera_auto_detect=0", "dtoverlay=<sensor>"],
                "cam0_line": "dtoverlay=<sensor>,cam0",
                "sensors": {"ov5647": "v1"},
            },
        ),
        pytest.raises(RuntimeError, match=r"dtoverlay=<sensor>,cam0"),
    ):
        manager.detect_and_select()


@pytest.mark.parametrize(
    "model, expected",
    [
        ("Raspberry Pi Compute Module 4 Rev 1.1", True),
        ("Raspberry Pi 4 Model B Rev 1.5", True),
        ("Raspberry Pi 5 Model B Rev 1.0", False),
        ("Raspberry Pi Compute Module 5 Rev 1.0", False),
        ("Raspberry Pi 500 Rev 1.0", False),
        (None, False),
    ],
)
def test_hardware_h264_follows_board_generation(model, expected):
    from bambucam import system_info

    with patch.object(system_info, "raspberry_pi_model", return_value=model):
        assert system_info.has_hardware_h264() is expected
