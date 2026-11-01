"""Prevent installer regressions in atomic configuration persistence permissions."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_installer_grants_service_group_private_directory_write_access():
    installer = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

    # Config.save() writes a temporary YAML in the same directory before
    # os.replace(), so file ownership alone cannot make settings writable.
    assert 'install -d -m 755 "$BAMBUCAM_DIR" "$BAMBUCAM_CONFIG_DIR"' not in installer
    assert 'install -d -o root -g "$SERVICE_USER" -m 2770 "$BAMBUCAM_CONFIG_DIR"' in installer


def test_atomic_config_writer_uses_target_directory():
    config = (ROOT / "bambucam" / "config.py").read_text(encoding="utf-8")
    assert "tempfile.NamedTemporaryFile(" in config
    assert "dir=path.parent" in config
    assert "os.replace(tmp_path, path)" in config
