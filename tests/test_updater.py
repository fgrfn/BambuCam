"""Tests for the updater service (mocked — no real network calls)."""

from unittest.mock import patch

from bambucam.updater import (
    ReleaseInfo,
    Updater,
    UpdateState,
    _parse_version,
    _version_gt,
)

# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


class TestVersionParsing:
    def test_basic(self):
        assert _parse_version("1.2.3") == (1, 2, 3)

    def test_strips_v_prefix(self):
        assert _parse_version("v1.2.3") == (1, 2, 3)

    def test_strips_prerelease_suffix(self):
        assert _parse_version("1.2.3-beta") == (1, 2, 3)

    def test_version_gt_true(self):
        assert _version_gt("1.1.0", "1.0.0")
        assert _version_gt("2.0.0", "1.9.9")
        assert _version_gt("1.0.1", "1.0.0")

    def test_version_gt_false(self):
        assert not _version_gt("1.0.0", "1.0.0")
        assert not _version_gt("0.9.9", "1.0.0")


# ---------------------------------------------------------------------------
# Updater.check()
# ---------------------------------------------------------------------------

_MOCK_RELEASE = ReleaseInfo(
    version="1.0.0",
    tag="v1.0.0",
    name="BambuCam v1.0.0",
    body="## Changelog\n- First release",
    published_at="2024-01-01T00:00:00Z",
    tarball_url="https://api.github.com/repos/fgrfn/bambucam/tarball/v1.0.0",
    html_url="https://github.com/fgrfn/bambucam/releases/tag/v1.0.0",
    is_prerelease=False,
)


class TestUpdaterCheck:
    def test_update_available(self):
        updater = Updater(current_version="0.1.0")
        with patch.object(updater, "_fetch_latest_release", return_value=_MOCK_RELEASE):
            status = updater.check()

        assert status.state == UpdateState.AVAILABLE
        assert status.update_available is True
        assert status.latest_version == "1.0.0"
        assert status.current_version == "0.1.0"

    def test_up_to_date(self):
        updater = Updater(current_version="1.0.0")
        with patch.object(updater, "_fetch_latest_release", return_value=_MOCK_RELEASE):
            status = updater.check()

        assert status.state == UpdateState.UP_TO_DATE
        assert status.update_available is False

    def test_newer_than_release(self):
        updater = Updater(current_version="2.0.0")
        with patch.object(updater, "_fetch_latest_release", return_value=_MOCK_RELEASE):
            status = updater.check()

        assert status.state == UpdateState.UP_TO_DATE
        assert status.update_available is False

    def test_network_error(self):
        updater = Updater(current_version="0.1.0")
        with patch.object(updater, "_fetch_latest_release", side_effect=ConnectionError("timeout")):
            status = updater.check()

        assert status.state == UpdateState.ERROR
        assert "timeout" in status.error

    def test_check_sets_checked_at(self):
        updater = Updater(current_version="0.1.0")
        with patch.object(updater, "_fetch_latest_release", return_value=_MOCK_RELEASE):
            status = updater.check()

        assert status.checked_at is not None


# ---------------------------------------------------------------------------
# Updater.start_update() guards
# ---------------------------------------------------------------------------


class TestUpdaterStartGuards:
    def test_cannot_start_without_check(self):
        updater = Updater(current_version="0.1.0")
        result = updater.start_update()
        assert result is False

    def test_cannot_start_when_up_to_date(self):
        updater = Updater(current_version="1.0.0")
        with patch.object(updater, "_fetch_latest_release", return_value=_MOCK_RELEASE):
            updater.check()
        result = updater.start_update()
        assert result is False


# ---------------------------------------------------------------------------
# UpdateStatus.as_dict()
# ---------------------------------------------------------------------------


class TestUpdateStatusAsDict:
    def test_idle_dict(self):
        updater = Updater(current_version="0.1.0")
        d = updater.status.as_dict()
        assert d["state"] == "idle"
        assert d["current_version"] == "0.1.0"
        assert d["update_available"] is False
        assert "latest_release" not in d

    def test_available_dict_has_release(self):
        updater = Updater(current_version="0.1.0")
        with patch.object(updater, "_fetch_latest_release", return_value=_MOCK_RELEASE):
            updater.check()
        d = updater.status.as_dict()
        assert d["state"] == "available"
        assert "latest_release" in d
        assert d["latest_release"]["version"] == "1.0.0"
        assert d["latest_release"]["html_url"] == _MOCK_RELEASE.html_url
