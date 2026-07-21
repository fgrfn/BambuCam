"""Tests for the updater service without real network or package installs."""

import io
import tarfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bambucam.update_guard import _healthy as guard_healthy
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
            FakeResponse(f"{digest}  bambucam-1.0.0-py3-none-any.whl\n".encode()),
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
            FakeResponse(f"{'0' * 64}  bambucam-1.0.0-py3-none-any.whl\n".encode()),
            FakeResponse(package, content_length=len(package)),
        ]
        with patch("bambucam.updater.requests.get", side_effect=responses):
            with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
                _updater()._download(release)


class TestRollback:
    def test_failed_health_check_restores_previous_installation(self, tmp_path):
        update_root = tmp_path / "update"
        update_root.mkdir()
        package = update_root / "bambucam.whl"
        package.write_bytes(b"wheel")
        backup = (tmp_path / "site", update_root / "rollback")
        updater = _updater()

        with (
            patch.object(updater, "_download", return_value=package),
            patch.object(updater, "_backup_installation", return_value=backup),
            patch.object(updater, "_install"),
            patch.object(
                updater,
                "_health_check_installation",
                side_effect=[RuntimeError("health failed"), None],
            ),
            patch.object(updater, "_restore_installation") as restore,
            patch.object(updater, "_restart") as restart,
        ):
            updater._update_pipeline(_MOCK_RELEASE)

        restore.assert_called_once_with(backup)
        restart.assert_not_called()
        assert updater.status.state == UpdateState.ERROR
        assert updater.status.rollback_performed is True
        assert "restored v0.1.0" in updater.status.error

    def test_package_backup_can_be_restored(self, tmp_path):
        site = tmp_path / "site-packages"
        package = site / "bambucam"
        metadata = site / "bambucam-1.2.2.dist-info"
        package.mkdir(parents=True)
        metadata.mkdir()
        (package / "module.py").write_text("old", encoding="utf-8")
        (metadata / "METADATA").write_text("Version: 1.2.2", encoding="utf-8")
        distribution = SimpleNamespace(locate_file=lambda _name: site)

        with patch("bambucam.updater.importlib.metadata.distribution", return_value=distribution):
            backup = Updater._backup_installation(tmp_path / "backup")

        (package / "module.py").write_text("new", encoding="utf-8")
        Updater._restore_installation(backup)

        assert (package / "module.py").read_text(encoding="utf-8") == "old"
        assert (metadata / "METADATA").read_text(encoding="utf-8") == "Version: 1.2.2"

    def test_post_restart_guard_is_launched_from_backup(self, tmp_path):
        backup_dir = tmp_path / "backup"
        script = backup_dir / "bambucam" / "update_guard.py"
        script.parent.mkdir(parents=True)
        script.write_text("# guard", encoding="utf-8")
        process = MagicMock()
        updater = _updater(health_url="http://127.0.0.1:9090/health")

        with patch("bambucam.updater.subprocess.Popen", return_value=process) as popen:
            result = updater._start_post_restart_guard(
                (tmp_path / "site", backup_dir),
                "1.3.0",
                tmp_path / "update",
            )

        command = popen.call_args.args[0]
        assert result is process
        assert str(script) in command
        assert "http://127.0.0.1:9090/health" in command
        assert "1.3.0" in command


class GuardResponse:
    def __init__(self, payload):
        import json

        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.payload


def test_guard_health_requires_expected_running_version():
    with patch("bambucam.update_guard.urlopen", return_value=GuardResponse({"version": "1.3.0"})):
        assert guard_healthy("http://127.0.0.1:8080/health", "1.3.0") is True
        assert guard_healthy("http://127.0.0.1:8080/health", "1.2.2") is False


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
