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
