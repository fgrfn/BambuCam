"""BambuCam - Raspberry Pi camera streaming for BambuBuddy."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("bambucam")
except PackageNotFoundError:
    __version__ = "0.0.0"

__author__ = "BambuCam Contributors"
