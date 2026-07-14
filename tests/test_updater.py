"""Tests for the updater service without real network or package installs."""

import io
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from bambucam.updater import (
    ReleaseInfo,
    Updater,
    UpdateState,
    _parse_checksum_file,
    _parse_version,
    _safe_extract_tar,
    _version_gt,
)

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


def _updater(version="0.1.0", **kwargs) -> Updater:
    return Updater(current_version=version, auto_check=False, **kwargs)


class TestVersionParsing:
    def test_basic(self):
        assert _parse_version("1.2.3") == (1, 2, 3)

    def test_strips_v_prefix(self):
        assert _parse_version("v1.2.3") == (1, 2, 3)

    def test_strips_prerelease_suffix(self):
        assert _parse_version("1.2.3-beta") == (1, 2, 3)

    def test_version_comparison(self):
        assert _version_gt("1.1.0", "1.0.0")
        assert _version_gt("2.0.0", "1.9.9")
        assert not _version_gt("1.0.0", "1.0.0")
        assert not _version_gt("0.9.9", "1.0.0")


class TestUpdaterCheck:
    def test_update_available(self):
        updater = _updater()
        with patch.object(updater, "_fetch_latest_release", return_value=_MOCK_RELEASE):
            status = updater.check()

        assert status.state == UpdateState.AVAILABLE
        assert status.update_available is True
        assert status.latest_version == "1.0.0"
        assert status.current_version == "0.1.0"

    def test_up_to_date(self):
        updater = _updater("1.0.0")
        with patch.object(updater, "_fetch_latest_release", return_value=_MOCK_RELEASE):
            status = updater.check()
        assert status.state == UpdateState.UP_TO_DATE
        assert status.update_available is False

    def test_network_error(self):
        updater = _updater()
        with patch.object(
            updater,
            "_fetch_latest_release",
            side_effect=ConnectionError("timeout"),
        ):
            status = updater.check()
        assert status.state == UpdateState.ERROR
        assert "timeout" in status.error

    def test_status_is_a_copy(self):
        updater = _updater()
        status = updater.status
        status.message = "mutated"
        assert updater.status.message != "mutated"


class TestUpdaterStartGuards:
    def test_cannot_start_without_check(self):
        assert _updater().start_update() is False

    def test_cannot_start_when_up_to_date(self):
        updater = _updater("1.0.0")
        with patch.object(updater, "_fetch_latest_release", return_value=_MOCK_RELEASE):
            updater.check()
        assert updater.start_update() is False

    def test_reserves_update_before_worker_starts(self):
        updater = _updater()
        with patch.object(updater, "_fetch_latest_release", return_value=_MOCK_RELEASE):
            updater.check()
        with patch.object(updater, "_update_pipeline"):
            assert updater.start_update() is True
            assert updater.start_update() is False
            assert updater.status.state == UpdateState.DOWNLOADING

    def test_missing_requested_release_sets_error(self):
        updater = _updater()
        with patch.object(updater, "_find_release", return_value=None):
            assert updater.start_update("9.9.9") is False
        assert updater.status.state == UpdateState.ERROR


class TestChecksums:
    def test_parses_standard_checksum_file(self):
        digest = "a" * 64
        parsed = _parse_checksum_file(f"{digest}  bambucam.whl\n")
        assert parsed == {"bambucam.whl": digest}

    @pytest.mark.parametrize(
        "line",
        [
            "not-a-hash  bambucam.whl",
            f"{'a' * 64}  ../bambucam.whl",
            "missing-filename",
        ],
    )
    def test_rejects_invalid_checksum_lines(self, line):
        with pytest.raises(RuntimeError):
            _parse_checksum_file(line)


class FakeResponse:
    def __init__(self, content: bytes, content_length: int = 0):
        self.content = content
        self.headers = {}
        if content_length:
            self.headers["content-length"] = str(content_length)

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=8192):
        del chunk_size
        yield self.content


class TestDownloads:
    def test_download_enforces_actual_size_limit(self, tmp_path):
        updater = _updater(max_package_bytes=4)
        with patch(
            "bambucam.updater.requests.get",
            return_value=FakeResponse(b"12345"),
        ):
            with pytest.raises(RuntimeError, match="exceeded"):
                updater._download_to_file("https://example.invalid/file", tmp_path / "file", 4)

    def test_release_download_verifies_sha256(self):
        package = b"wheel bytes"
        import hashlib

        digest = hashlib.sha256(package).hexdigest()
        release = ReleaseInfo(
            **{
                **_MOCK_RELEASE.__dict__,
                "assets": [
                    {
                        "name": "bambucam-1.0.0-py3-none-any.whl",
                        "url": "https://example.invalid/wheel",
                        "size": len(package),
                    },
                    {
                        "name": "SHA256SUMS",
                        "url": "https://example.invalid/checksums",
                        "size": 100,
                    },
                ],
            }
        )
        responses = [
            FakeResponse(
                f"{digest}  bambucam-1.0.0-py3-none-any.whl\n".encode()
            ),
            FakeResponse(package, content_length=len(package)),
        ]
        updater = _updater()
        with patch("bambucam.updater.requests.get", side_effect=responses):
            path = updater._download(release)
        try:
            assert path.read_bytes() == package
        finally:
            import shutil

            shutil.rmtree(path.parent)

    def test_release_download_rejects_bad_checksum(self):
        package = b"wheel bytes"
        release = ReleaseInfo(
            **{
                **_MOCK_RELEASE.__dict__,
                "assets": [
                    {
                        "name": "bambucam-1.0.0-py3-none-any.whl",
                        "url": "https://example.invalid/wheel",
                        "size": len(package),
                    },
                    {
                        "name": "SHA256SUMS",
                        "url": "https://example.invalid/checksums",
                        "size": 100,
                    },
                ],
            }
        )
        responses = [
            FakeResponse(
                f"{'0' * 64}  bambucam-1.0.0-py3-none-any.whl\n".encode()
            ),
            FakeResponse(package, content_length=len(package)),
        ]
        with patch("bambucam.updater.requests.get", side_effect=responses):
            with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
                _updater()._download(release)


class TestSafeTarExtraction:
    def test_extracts_regular_project(self, tmp_path):
        archive = tmp_path / "source.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            data = b"[project]\nname='bambucam'\n"
            info = tarfile.TarInfo("project/pyproject.toml")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        destination = tmp_path / "out"
        destination.mkdir()
        _safe_extract_tar(archive, destination)
        assert (destination / "project" / "pyproject.toml").is_file()

    def test_rejects_path_traversal(self, tmp_path):
        archive = tmp_path / "malicious.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            data = b"bad"
            info = tarfile.TarInfo("../outside.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        destination = tmp_path / "out"
        destination.mkdir()
        with pytest.raises(RuntimeError, match="Unsafe path"):
            _safe_extract_tar(archive, destination)


class TestUpdateStatusAsDict:
    def test_idle_dict(self):
        data = _updater().status.as_dict()
        assert data["state"] == "idle"
        assert data["current_version"] == "0.1.0"
        assert data["update_available"] is False
        assert "latest_release" not in data

    def test_available_dict_has_release(self):
        updater = _updater()
        with patch.object(updater, "_fetch_latest_release", return_value=_MOCK_RELEASE):
            updater.check()
        data = updater.status.as_dict()
        assert data["state"] == "available"
        assert data["latest_release"]["version"] == "1.0.0"
