#!/usr/bin/env bash
# BambuCam Installer for Raspberry Pi OS (Bullseye / Bookworm)
# Run as root: sudo bash install.sh
set -euo pipefail

BAMBUCAM_VERSION="0.1.0"
MEDIAMTX_VERSION="v1.9.3"
INSTALL_DIR="/opt/bambucam"
CONFIG_DIR="/etc/bambucam"
DATA_DIR="/var/lib/bambucam"
SERVICE_USER="bambucam"

ARCH=$(dpkg --print-architecture)
case "$ARCH" in
  armhf)   MEDIAMTX_ARCH="linux_armv7"  ;;
  arm64)   MEDIAMTX_ARCH="linux_arm64"  ;;
  amd64)   MEDIAMTX_ARCH="linux_amd64"  ;;
  *)       echo "Unsupported architecture: $ARCH"; exit 1 ;;
esac

MEDIAMTX_URL="https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_${MEDIAMTX_ARCH}.tar.gz"

# ---------------------------------------------------------------------------
# Colour output
# ---------------------------------------------------------------------------
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
step()  { echo -e "\n${GREEN}▶ $*${NC}"; }

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
step "Checking system requirements"
[[ $EUID -eq 0 ]] || error "This script must be run as root (sudo bash install.sh)"
command -v apt-get &>/dev/null || error "apt-get not found — Raspberry Pi OS required"

step "Updating package lists"
apt-get update -qq

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------
step "Installing system packages"
apt-get install -y --no-install-recommends \
  python3 python3-pip python3-venv \
  python3-picamera2 \
  ffmpeg \
  libcamera-apps \
  v4l-utils \
  curl \
  git

# ---------------------------------------------------------------------------
# Create user & directories
# ---------------------------------------------------------------------------
step "Creating system user and directories"
if ! id "$SERVICE_USER" &>/dev/null; then
  useradd --system --no-create-home --groups video,gpio,i2c \
    --shell /usr/sbin/nologin "$SERVICE_USER"
  info "Created user: $SERVICE_USER"
fi

install -d -m 755 "$INSTALL_DIR"
install -d -m 755 "$CONFIG_DIR"
install -d -m 750 "$DATA_DIR"
install -d -m 750 "$DATA_DIR/snapshots"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"

# ---------------------------------------------------------------------------
# MediaMTX
# ---------------------------------------------------------------------------
step "Downloading MediaMTX ${MEDIAMTX_VERSION}"
TMPDIR=$(mktemp -d)
curl -fsSL "$MEDIAMTX_URL" -o "$TMPDIR/mediamtx.tar.gz"
tar -xzf "$TMPDIR/mediamtx.tar.gz" -C "$TMPDIR"
install -m 755 "$TMPDIR/mediamtx" /usr/local/bin/mediamtx
rm -rf "$TMPDIR"
info "MediaMTX installed to /usr/local/bin/mediamtx"

# ---------------------------------------------------------------------------
# BambuCam Python package
# ---------------------------------------------------------------------------
step "Installing BambuCam"

# Copy source (or install from git if running from a temp location)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(dirname "$SCRIPT_DIR")"

if [[ -f "$SOURCE_ROOT/pyproject.toml" ]]; then
  info "Installing from local source: $SOURCE_ROOT"
  python3 -m venv "$INSTALL_DIR/venv"
  # Use system picamera2 (installed via apt above)
  "$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
  "$INSTALL_DIR/venv/bin/pip" install --quiet \
    --system-site-packages \
    "$SOURCE_ROOT"
else
  warn "Source not found at $SOURCE_ROOT — installing from PyPI (when published)"
  python3 -m venv "$INSTALL_DIR/venv"
  "$INSTALL_DIR/venv/bin/pip" install --quiet bambucam
fi

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
step "Installing configuration"
if [[ ! -f "$CONFIG_DIR/bambucam.yaml" ]]; then
  install -m 640 "$SOURCE_ROOT/config/bambucam.yaml" "$CONFIG_DIR/bambucam.yaml"
  chown "root:$SERVICE_USER" "$CONFIG_DIR/bambucam.yaml"
  info "Default config installed to $CONFIG_DIR/bambucam.yaml"
else
  info "Existing config kept: $CONFIG_DIR/bambucam.yaml"
fi

# Create empty environment file for systemd
touch "$CONFIG_DIR/environment"
chmod 640 "$CONFIG_DIR/environment"
chown "root:$SERVICE_USER" "$CONFIG_DIR/environment"

# ---------------------------------------------------------------------------
# systemd service
# ---------------------------------------------------------------------------
step "Installing systemd service"
install -m 644 "$SOURCE_ROOT/systemd/bambucam.service" /etc/systemd/system/bambucam.service
systemctl daemon-reload
systemctl enable bambucam.service
info "Service installed and enabled"

# ---------------------------------------------------------------------------
# Camera permissions
# ---------------------------------------------------------------------------
step "Configuring camera access"
# Add service user to video group (already done at creation, but just in case)
usermod -aG video "$SERVICE_USER" 2>/dev/null || true

# Enable camera in raspi-config if not already done
if command -v raspi-config &>/dev/null; then
  raspi-config nonint do_camera 0 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Firewall hint
# ---------------------------------------------------------------------------
warn "Make sure the following ports are accessible on your network:"
warn "  8080 — WebUI + MJPEG stream"
warn "  8554 — RTSP (for BambuBuddy)"
warn "  8888 — HLS"
warn "If you use ufw: sudo ufw allow 8080,8554,8888/tcp"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
step "Installation complete!"
echo ""
echo -e "  Start now :  ${GREEN}sudo systemctl start bambucam${NC}"
echo -e "  Logs      :  ${GREEN}journalctl -u bambucam -f${NC}"
echo -e "  WebUI     :  ${GREEN}http://$(hostname -I | awk '{print $1}'):8080${NC}"
echo -e "  RTSP URL  :  ${GREEN}rtsp://$(hostname -I | awk '{print $1}'):8554/cam${NC}"
echo ""
echo -e "  Edit config: ${YELLOW}sudo nano $CONFIG_DIR/bambucam.yaml${NC}"
echo ""
