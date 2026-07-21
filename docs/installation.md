# Installation Guide

## Requirements

- Raspberry Pi 2, 3, 4, 5, or another Debian-based computer for USB webcams
- Raspberry Pi OS Bullseye/Bookworm or a compatible Debian release
- A supported CSI camera or V4L2 USB webcam
- Internet access during installation

## Recommended release installation

Use the installer attached to the latest GitHub release:

```bash
curl -fsSL https://github.com/fgrfn/bambucam/releases/latest/download/install.sh | sudo bash
sudo systemctl start bambucam
```

For newly published releases, the installer downloads the complete source bundle and wheel, verifies both against the release `SHA256SUMS`, and only then installs them. Older releases without verified bundles fall back to GitHub's source archive with a warning.

### Installation variants

```bash
# Install a specific tagged release
curl -fsSL https://github.com/fgrfn/bambucam/releases/latest/download/install.sh \
  | sudo BAMBUCAM_VERSION=1.0.6 bash

# Use a different application directory
curl -fsSL https://github.com/fgrfn/bambucam/releases/latest/download/install.sh \
  | sudo BAMBUCAM_DIR=/srv/bambucam bash

# Install a development branch (not release-checksummed)
curl -fsSL https://raw.githubusercontent.com/fgrfn/bambucam/main/scripts/install.sh \
  | sudo BAMBUCAM_BRANCH=main bash

# Install from a local clone
 git clone https://github.com/fgrfn/bambucam
 sudo bash bambucam/scripts/install.sh
```

`BAMBUCAM_DIR` must be an absolute path without whitespace. The installer renders the systemd unit with that path, so self-updates and service startup continue to work outside `/opt/bambucam`.

The installer:

- installs Python, ffmpeg, V4L2 tools, OpenCV, and Raspberry Pi camera packages where available;
- installs the pinned MediaMTX server;
- creates the restricted `bambucam` service account;
- creates a virtual environment below `BAMBUCAM_DIR`;
- preserves an existing `/etc/bambucam/bambucam.yaml`;
- installs and hardens the systemd service;
- restarts the service automatically when updating a running installation.

## Verify release assets manually

A release can be downloaded and verified before running its installer:

```bash
VERSION=1.0.6
BASE="https://github.com/fgrfn/bambucam/releases/download/v${VERSION}"
curl -fLO "${BASE}/install.sh"
curl -fLO "${BASE}/SHA256SUMS"
grep ' install.sh$' SHA256SUMS | sha256sum -c -
sudo bash install.sh
```

## Service management

```bash
sudo systemctl start bambucam
sudo systemctl stop bambucam
sudo systemctl restart bambucam
sudo systemctl enable bambucam
systemctl status bambucam
journalctl -u bambucam -f
```

## Verify the installation

The executable lives inside the configured installation directory. With the default path:

```bash
/opt/bambucam/venv/bin/bambucam --version
sudo -u bambucam /opt/bambucam/venv/bin/bambucam --list-cameras
curl -s http://localhost:8080/health | python3 -m json.tool
curl -s http://localhost:8080/snapshot | file -
ffplay rtsp://localhost:8554/cam
```

When WebUI authentication is enabled, API calls require HTTP Basic authentication or the configured Bearer token. State-changing Basic-auth requests also require a same-origin browser request or the `X-BambuCam-CSRF: 1` header.

## Updating

The preferred method is **WebUI → Software Update**. The updater:

- prevents concurrent update jobs;
- limits download sizes;
- verifies `SHA256SUMS` when provided;
- rejects unsafe paths and special files in source archives;
- verifies that the installed package reports the expected release version;
- backs up the installed package and runs an isolated import/configuration health check;
- re-executes the process and verifies the expected version through local `/health`;
- restores and restarts the previous installed version automatically if installation or post-restart health verification fails.

Configuration files are not replaced by upgrades. The versioned configuration schema migrates older files while preserving explicit camera, bitrate, stream, authentication, and retention settings.

Re-running the release installer is also supported and preserves the existing configuration:

```bash
curl -fsSL https://github.com/fgrfn/bambucam/releases/latest/download/install.sh | sudo bash
```

## Manual development installation

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-picamera2 ffmpeg v4l-utils

git clone https://github.com/fgrfn/bambucam
cd bambucam
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -e ".[dev]"
pytest tests/
bambucam --config ./config/bambucam.yaml
```

## Uninstalling

```bash
sudo systemctl disable --now bambucam
sudo rm -f /etc/systemd/system/bambucam.service
sudo systemctl daemon-reload
sudo rm -rf /opt/bambucam             # adjust when BAMBUCAM_DIR was changed
sudo rm -rf /etc/bambucam             # removes configuration and credentials
sudo rm -rf /var/lib/bambucam         # optional: removes snapshots and data
sudo userdel bambucam                 # optional
```
