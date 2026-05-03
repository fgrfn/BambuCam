#!/usr/bin/env bash
# ============================================================================
# BambuCam Installer for Raspberry Pi OS (Bullseye / Bookworm)
#
# CURL ONE-LINER (recommended):
#   curl -fsSL https://raw.githubusercontent.com/fgrfn/bambucam/main/scripts/install.sh | sudo bash
#
# SPECIFIC VERSION:
#   curl -fsSL https://github.com/fgrfn/bambucam/releases/latest/download/install.sh | sudo bash
#
# FROM LOCAL CLONE (development):
#   sudo bash scripts/install.sh
#
# OPTIONS (env vars):
#   BAMBUCAM_VERSION=0.2.0  — install a specific version (default: latest)
#   BAMBUCAM_BRANCH=main    — install from a branch instead of a release
#   BAMBUCAM_DIR=/opt/bambucam  — install directory (default: /opt/bambucam)
# ============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BAMBUCAM_REPO="fgrfn/bambucam"
BAMBUCAM_DIR="${BAMBUCAM_DIR:-/opt/bambucam}"
BAMBUCAM_CONFIG_DIR="/etc/bambucam"
BAMBUCAM_DATA_DIR="/var/lib/bambucam"
SERVICE_USER="bambucam"
MEDIAMTX_VERSION="v1.9.3"

# Detect CPU architecture for MediaMTX
ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m)
case "$ARCH" in
  armhf|armv7*)  MEDIAMTX_ARCH="linux_armv7"  ;;
  arm64|aarch64) MEDIAMTX_ARCH="linux_arm64"  ;;
  amd64|x86_64)  MEDIAMTX_ARCH="linux_amd64"  ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

MEDIAMTX_URL="https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_${MEDIAMTX_ARCH}.tar.gz"
GITHUB_RAW="https://raw.githubusercontent.com/${BAMBUCAM_REPO}"
GITHUB_API="https://api.github.com/repos/${BAMBUCAM_REPO}"

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
if [ -t 1 ]; then
  GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
  CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
else
  GREEN=''; YELLOW=''; RED=''; CYAN=''; BOLD=''; NC=''
fi

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
step()  { echo -e "\n${CYAN}${BOLD}▶ $*${NC}"; }
banner() {
  echo -e "${GREEN}"
  echo "  ____                 _            ____"
  echo " | __ )  __ _ _ __ ___ | |__  _   _/ ___|__ _ _ __ ___"
  echo " |  _ \ / _\` | '_ \` _ \| '_ \| | | | |   / _\` | '_ \` _ \\"
  echo " | |_) | (_| | | | | | | |_) | |_| | |__| (_| | | | | | |"
  echo " |____/ \__,_|_| |_| |_|_.__/ \__,_|\____\__,_|_| |_| |_|"
  echo -e "${NC}"
  echo "  Raspberry Pi Camera Streaming for BambuBuddy"
  echo "  https://github.com/${BAMBUCAM_REPO}"
  echo ""
}

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
banner
step "Checking requirements"
[[ $EUID -eq 0 ]] || error "Run as root: curl -fsSL ... | sudo bash"
command -v apt-get &>/dev/null || error "apt-get not found — Raspberry Pi OS required"
command -v curl   &>/dev/null || { apt-get install -y curl -qq; }

# ---------------------------------------------------------------------------
# Determine install source
# ---------------------------------------------------------------------------
# Mode 1: Running from a local git clone (development / CI)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-install.sh}")" 2>/dev/null && pwd || echo "")"
SOURCE_ROOT="$(dirname "$SCRIPT_DIR" 2>/dev/null || echo "")"
LOCAL_SOURCE=false

if [[ -f "$SOURCE_ROOT/pyproject.toml" ]]; then
  LOCAL_SOURCE=true
  info "Local source tree detected at $SOURCE_ROOT — installing from source"
fi

# Mode 2: Specific version requested via env var
if [[ -n "${BAMBUCAM_VERSION:-}" ]]; then
  INSTALL_TAG="v${BAMBUCAM_VERSION#v}"
  info "Target version: $INSTALL_TAG"
elif [[ -n "${BAMBUCAM_BRANCH:-}" ]]; then
  INSTALL_TAG=""
  INSTALL_BRANCH="$BAMBUCAM_BRANCH"
  info "Target branch: $INSTALL_BRANCH"
elif [[ "$LOCAL_SOURCE" == "false" ]]; then
  step "Fetching latest release info from GitHub"
  LATEST_JSON=$(curl -sSL "${GITHUB_API}/releases/latest" 2>/dev/null || true)
  INSTALL_TAG=$(echo "$LATEST_JSON" | grep '"tag_name"' | head -1 | cut -d'"' -f4)
  if [[ -z "$INSTALL_TAG" ]]; then
    warn "No release found on GitHub — installing from main branch"
    INSTALL_BRANCH="main"
    INSTALL_TAG=""
  else
    BAMBUCAM_VERSION="${INSTALL_TAG#v}"
    info "Latest release: $INSTALL_TAG (v$BAMBUCAM_VERSION)"
  fi
fi

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------
step "Installing system packages"
apt-get update -qq

# Core packages — required on all platforms
apt-get install -y --no-install-recommends \
  python3 python3-pip python3-venv python3-dev \
  gcc \
  ffmpeg \
  v4l-utils \
  curl \
  git

# Raspberry Pi OS specific — silently skip on other distros
_RPI_PKGS=(python3-picamera2 libcamera-apps)
_MISSING=()
for _pkg in "${_RPI_PKGS[@]}"; do
  if apt-cache show "$_pkg" &>/dev/null 2>&1; then
    apt-get install -y --no-install-recommends "$_pkg" -qq || true
  else
    _MISSING+=("$_pkg")
  fi
done
if [[ ${#_MISSING[@]} -gt 0 ]]; then
  warn "RPi-specific packages not found in apt: ${_MISSING[*]}"
  warn "  → CSI camera modules (picamera2) may not work on this system."
  warn "  → USB webcams via V4L2 will still work."
fi

# ---------------------------------------------------------------------------
# Download BambuCam source (if not running from local clone)
# ---------------------------------------------------------------------------
SRC_DIR="$SOURCE_ROOT"

if [[ "$LOCAL_SOURCE" == "false" ]]; then
  _INSTALL_REF="${INSTALL_TAG:-${INSTALL_BRANCH:-main}}"
  step "Downloading BambuCam ${_INSTALL_REF}"
  TMP_SRC=$(mktemp -d)
  TARBALL_URL="https://api.github.com/repos/${BAMBUCAM_REPO}/tarball/${_INSTALL_REF}"
  info "Downloading from $TARBALL_URL"
  curl -fsSL "$TARBALL_URL" | tar -xz -C "$TMP_SRC" --strip-components=1
  SRC_DIR="$TMP_SRC"
fi

# ---------------------------------------------------------------------------
# Create system user & directories
# ---------------------------------------------------------------------------
step "Creating system user and directories"
if ! id "$SERVICE_USER" &>/dev/null; then
  useradd --system --no-create-home \
    --shell /usr/sbin/nologin \
    "$SERVICE_USER"
  info "Created user: $SERVICE_USER"
else
  info "User already exists: $SERVICE_USER"
fi

usermod -aG video "$SERVICE_USER" 2>/dev/null || true
usermod -aG gpio  "$SERVICE_USER" 2>/dev/null || true
usermod -aG i2c   "$SERVICE_USER" 2>/dev/null || true

install -d -m 755 "$BAMBUCAM_DIR"
install -d -m 755 "$BAMBUCAM_CONFIG_DIR"
install -d -m 750 "$BAMBUCAM_DATA_DIR"
install -d -m 750 "$BAMBUCAM_DATA_DIR/snapshots"
chown -R "$SERVICE_USER:$SERVICE_USER" "$BAMBUCAM_DATA_DIR"

# ---------------------------------------------------------------------------
# MediaMTX
# ---------------------------------------------------------------------------
step "Installing MediaMTX ${MEDIAMTX_VERSION} (RTSP server)"
MTMP=$(mktemp -d)
curl -fsSL "$MEDIAMTX_URL" -o "$MTMP/mediamtx.tar.gz"
tar -xzf "$MTMP/mediamtx.tar.gz" -C "$MTMP"
install -m 755 "$MTMP/mediamtx" /usr/local/bin/mediamtx
rm -rf "$MTMP"
info "MediaMTX → /usr/local/bin/mediamtx"

# ---------------------------------------------------------------------------
# Python virtual environment & BambuCam package
# ---------------------------------------------------------------------------
step "Installing BambuCam Python package"

# Re-use existing venv if present (keeps user data intact on update)
# --system-site-packages lets the venv see python3-picamera2 installed via apt
if [[ ! -d "$BAMBUCAM_DIR/venv" ]]; then
  python3 -m venv --system-site-packages "$BAMBUCAM_DIR/venv"
fi

PIP="$BAMBUCAM_DIR/venv/bin/pip"

"$PIP" install --quiet --upgrade pip
"$PIP" install --quiet "$SRC_DIR"

info "BambuCam $(${BAMBUCAM_DIR}/venv/bin/bambucam --version 2>/dev/null || echo installed)"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
step "Installing default configuration"
if [[ ! -f "$BAMBUCAM_CONFIG_DIR/bambucam.yaml" ]]; then
  install -m 640 "$SRC_DIR/config/bambucam.yaml" \
    "$BAMBUCAM_CONFIG_DIR/bambucam.yaml"
  chown "root:$SERVICE_USER" "$BAMBUCAM_CONFIG_DIR/bambucam.yaml"
  info "Config written to $BAMBUCAM_CONFIG_DIR/bambucam.yaml"
else
  info "Existing config preserved: $BAMBUCAM_CONFIG_DIR/bambucam.yaml"
fi

# Environment file for systemd overrides
if [[ ! -f "$BAMBUCAM_CONFIG_DIR/environment" ]]; then
  install -m 640 /dev/null "$BAMBUCAM_CONFIG_DIR/environment"
  chown "root:$SERVICE_USER" "$BAMBUCAM_CONFIG_DIR/environment"
fi

# ---------------------------------------------------------------------------
# systemd service
# ---------------------------------------------------------------------------
step "Installing systemd service"
install -m 644 "$SRC_DIR/systemd/bambucam.service" \
  /etc/systemd/system/bambucam.service

SYSTEMD_OK=false
if command -v systemctl &>/dev/null && systemctl --version &>/dev/null 2>&1; then
  if systemctl daemon-reload 2>/dev/null && systemctl enable bambucam.service 2>/dev/null; then
    SYSTEMD_OK=true
    info "Service enabled: bambucam.service"
  fi
fi
if [[ "$SYSTEMD_OK" == "false" ]]; then
  warn "systemd not available (LXC/container?) — service file installed but not enabled."
  warn "  Start BambuCam manually: sudo -u $SERVICE_USER $BAMBUCAM_DIR/venv/bin/bambucam"
fi

# ---------------------------------------------------------------------------
# Camera (raspi-config enable legacy camera interface if needed)
# ---------------------------------------------------------------------------
step "Configuring camera"
if command -v raspi-config &>/dev/null; then
  raspi-config nonint do_camera 0 2>/dev/null || true
  info "Camera interface enabled via raspi-config"
fi

# ---------------------------------------------------------------------------
# Firewall hints
# ---------------------------------------------------------------------------
if command -v ufw &>/dev/null && ufw status | grep -q "Status: active"; then
  step "Configuring firewall (ufw)"
  ufw allow 8080/tcp comment "BambuCam WebUI+MJPEG" 2>/dev/null || true
  ufw allow 8554/tcp comment "BambuCam RTSP"        2>/dev/null || true
  ufw allow 8888/tcp comment "BambuCam HLS"         2>/dev/null || true
  info "ufw rules added"
else
  warn "Remember to open ports if you use a firewall:"
  warn "  sudo ufw allow 8080,8554,8888/tcp"
fi

# ---------------------------------------------------------------------------
# Cleanup temp download dir (must happen after all $SRC_DIR files are used)
# ---------------------------------------------------------------------------
if [[ "$LOCAL_SOURCE" == "false" && -n "${TMP_SRC:-}" ]]; then
  rm -rf "$TMP_SRC"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "<pi-ip>")

echo ""
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  BambuCam installation complete!${NC}"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
if [[ "$SYSTEMD_OK" == "true" ]]; then
  echo -e "  ${BOLD}Start:${NC}     sudo systemctl start bambucam"
  echo -e "  ${BOLD}Logs:${NC}      journalctl -u bambucam -f"
else
  echo -e "  ${BOLD}Start:${NC}     sudo -u $SERVICE_USER $BAMBUCAM_DIR/venv/bin/bambucam"
  echo -e "  ${BOLD}Logs:${NC}      (stdout of the command above)"
fi
echo ""
echo -e "  ${BOLD}WebUI:${NC}     ${CYAN}http://${LOCAL_IP}:8080${NC}"
echo -e "  ${BOLD}RTSP URL:${NC}  ${CYAN}rtsp://${LOCAL_IP}:8554/cam${NC}  ← BambuBuddy"
echo -e "  ${BOLD}MJPEG:${NC}     ${CYAN}http://${LOCAL_IP}:8080/stream${NC}"
echo ""
echo -e "  ${BOLD}Config:${NC}    sudo nano ${BAMBUCAM_CONFIG_DIR}/bambucam.yaml"
echo ""
echo -e "  ${YELLOW}To update later: use the WebUI → Software-Update${NC}"
echo ""
