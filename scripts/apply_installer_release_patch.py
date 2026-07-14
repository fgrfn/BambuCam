"""One-time patch helper for installer/update release hardening."""

from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8")
    if old not in content:
        raise RuntimeError(f"Expected text not found in {path}: {old[:100]!r}")
    file_path.write_text(content.replace(old, new, 1), encoding="utf-8")


replace(
    "scripts/install.sh",
    '''make_temp_dir() {
  local directory
  directory=$(mktemp -d)
  _TMP_DIRS+=("$directory")
  printf '%s\\n' "$directory"
}
''',
    '''NEW_TEMP_DIR=""
make_temp_dir() {
  NEW_TEMP_DIR=$(mktemp -d)
  _TMP_DIRS+=("$NEW_TEMP_DIR")
}
''',
)
replace(
    "scripts/install.sh",
    "  DOWNLOAD_DIR=$(make_temp_dir)\n",
    "  make_temp_dir\n  DOWNLOAD_DIR=$NEW_TEMP_DIR\n",
)
replace(
    "scripts/install.sh",
    "MEDIAMTX_TMP=$(make_temp_dir)\n",
    "make_temp_dir\nMEDIAMTX_TMP=$NEW_TEMP_DIR\n",
)

replace(
    "systemd/bambucam.service",
    "SupplementaryGroups=video gpio i2c\n",
    "",
)

replace(
    "bambucam/updater.py",
    '''VENV_PIP = Path("/opt/bambucam/venv/bin/pip")
MAX_PACKAGE_BYTES''',
    '''MAX_PACKAGE_BYTES''',
)
replace(
    "bambucam/updater.py",
    '''        pip_path: Path = VENV_PIP,
        auto_check: bool = True,
''',
    '''        pip_path: Optional[Path] = None,
        auto_check: bool = True,
''',
)
replace(
    "bambucam/updater.py",
    '''        self._pip_path = Path(pip_path)
''',
    '''        self._pip_path = (
            Path(pip_path) if pip_path is not None else Path(sys.executable).parent / "pip"
        )
''',
)
