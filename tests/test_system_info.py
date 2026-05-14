"""Tests for system_info helpers."""

from unittest.mock import patch

import pytest

from bambucam.system_info import pi_capability_tier


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
        # Non-Pi hardware → assume capable
        (None, 3),
    ],
)
def test_pi_capability_tier(model_string, expected_tier):
    with patch("bambucam.system_info.raspberry_pi_model", return_value=model_string):
        assert pi_capability_tier() == expected_tier
