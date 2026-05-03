# Installation Guide

## Requirements

- Raspberry Pi 2 / 3 / 4 / 5
- Raspberry Pi OS Bullseye or Bookworm (64-bit or 32-bit)
- Compatible camera module (see [README](../README.md))
- Internet connection

---

## One-liner installation (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/fgrfn/bambucam/main/scripts/install.sh | sudo bash
```

After installation, start BambuCam:

```bash
sudo systemctl start bambucam
```

### Variants

```bash
# Always install the latest stable release (attached to GitHub Release):
curl -fsSL https://github.com/fgrfn/bambucam/releases/latest/download/install.sh | sudo bash

# Install a specific version:
BAMBUCAM_VERSION=0.2.0 bash <(curl -fsSL https://raw.githubusercontent.com/fgrfn/bambucam/main/scripts/install.sh)

# Install from a specific branch (e.g. for testing):
BAMBUCAM_BRANCH=feature/my-feature sudo bash <(curl -fsSL https://raw.githubusercontent.com/fgrfn/bambucam/main/scripts/install.sh)

# Install from a local clone (development):
git clone https://github.com/fgrfn/bambucam
sudo bash bambucam/scripts/install.sh
```

The installer:
- Installs system packages (`ffmpeg`, `libcamera-apps`, `python3-picamera2`, …)
- Downloads and installs **MediaMTX** (RTSP server binary)
- Creates a dedicated system user `bambucam`
- Installs BambuCam into `/opt/bambucam/`
- Writes default config to `/etc/bambucam/bambucam.yaml`
- Registers and enables the systemd service

---

## Manual installation

### 1. System packages

```bash
sudo apt update
sudo apt install -y \
  python3 python3-pip python3-venv \
  python3-picamera2 \
  ffmpeg libcamera-apps \
  v4l-utils
```

### 2. MediaMTX

Download from [github.com/bluenviron/mediamtx/releases](https://github.com/bluenviron/mediamtx/releases):

```bash
# ARM64 (RPi 4/5 with 64-bit OS)
curl -L https://github.com/bluenviron/mediamtx/releases/download/v1.9.3/mediamtx_v1.9.3_linux_arm64.tar.gz \
  | sudo tar -xz -C /usr/local/bin mediamtx

# ARMv7 (RPi 2/3 or 32-bit OS)
curl -L https://github.com/bluenviron/mediamtx/releases/download/v1.9.3/mediamtx_v1.9.3_linux_armv7.tar.gz \
  | sudo tar -xz -C /usr/local/bin mediamtx
```

### 3. Python package

```bash
python3 -m venv /opt/bambucam/venv
/opt/bambucam/venv/bin/pip install bambucam
```

Or install from source (development):

```bash
git clone https://github.com/fgrfn/bambucam
cd bambucam
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

### 4. Run

```bash
bambucam
# or
bambucam --config /path/to/bambucam.yaml
# or (debug mode)
bambucam --log-level DEBUG
```

---

## Service management

```bash
# Start
sudo systemctl start bambucam

# Stop
sudo systemctl stop bambucam

# Restart
sudo systemctl restart bambucam

# Enable on boot
sudo systemctl enable bambucam

# View logs
journalctl -u bambucam -f

# Status
systemctl status bambucam
```

---

## Verify installation

```bash
# List detected cameras
bambucam --list-cameras

# Test MJPEG stream (should return JPEG data)
curl -s http://localhost:8080/snapshot | file -

# Test API
curl http://localhost:8080/api/v1/camera/status | python3 -m json.tool

# Test RTSP (requires ffplay or VLC)
ffplay rtsp://localhost:8554/cam
```

---

## Updating

```bash
cd bambucam
git pull
sudo bash scripts/install.sh
sudo systemctl restart bambucam
```

---

## Uninstalling

```bash
sudo systemctl stop bambucam
sudo systemctl disable bambucam
sudo rm /etc/systemd/system/bambucam.service
sudo systemctl daemon-reload
sudo rm -rf /opt/bambucam
sudo rm -rf /etc/bambucam
# Optional: remove data
sudo rm -rf /var/lib/bambucam
# Optional: remove user
sudo userdel bambucam
```
