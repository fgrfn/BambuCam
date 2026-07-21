"""Tests for narrowly scoped host reboot scheduling."""

from unittest.mock import MagicMock, patch

import pytest

from bambucam import system_control


@pytest.fixture(autouse=True)
def reset_reboot_guard():
    system_control._reboot_pending = False
    yield
    system_control._reboot_pending = False


def test_reboot_is_only_available_on_linux_with_systemctl():
    with patch("bambucam.system_control.platform.system", return_value="Windows"):
        assert system_control.system_reboot_available() is False

    with (
        patch("bambucam.system_control.platform.system", return_value="Linux"),
        patch(
            "bambucam.system_control.shutil.which",
            side_effect=lambda name: f"/usr/bin/{name}",
        ),
        patch("bambucam.system_control.subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        assert system_control.system_reboot_available() is True


def test_system_reboot_is_scheduled_only_once():
    worker = MagicMock()
    with (
        patch("bambucam.system_control.platform.system", return_value="Linux"),
        patch(
            "bambucam.system_control.shutil.which",
            side_effect=lambda name: f"/usr/bin/{name}",
        ),
        patch("bambucam.system_control.subprocess.run") as run,
        patch("bambucam.system_control.threading.Thread", return_value=worker) as thread,
    ):
        run.return_value.returncode = 0
        assert system_control.schedule_system_reboot() is True
        assert system_control.schedule_system_reboot() is False

    thread.assert_called_once_with(
        target=system_control._delayed_system_reboot,
        args=("/usr/bin/systemctl",),
        daemon=True,
        name="bambucam-system-reboot",
    )
    worker.start.assert_called_once_with()


def test_delayed_reboot_invokes_systemctl_without_a_shell():
    system_control._reboot_pending = True
    with (
        patch("bambucam.system_control.time.sleep") as sleep,
        patch("bambucam.system_control.subprocess.run") as run,
    ):
        system_control._delayed_system_reboot("/usr/bin/systemctl")

    sleep.assert_called_once_with(1.0)
    run.assert_called_once_with(
        ["/usr/bin/systemctl", "--no-wall", "reboot"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert system_control._reboot_pending is False


def test_reboot_request_is_rejected_without_systemd():
    with patch("bambucam.system_control.platform.system", return_value="Windows"):
        with pytest.raises(RuntimeError, match="only available on Linux"):
            system_control.schedule_system_reboot()


def test_reboot_request_is_rejected_without_installed_policy():
    with (
        patch("bambucam.system_control.platform.system", return_value="Linux"),
        patch(
            "bambucam.system_control.shutil.which",
            side_effect=lambda name: f"/usr/bin/{name}",
        ),
        patch("bambucam.system_control.subprocess.run") as run,
    ):
        run.return_value.returncode = 1
        with pytest.raises(RuntimeError, match="permission is not installed"):
            system_control.schedule_system_reboot()
