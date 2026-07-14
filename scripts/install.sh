#!/usr/bin/env bash
# BambuCam installer for Raspberry Pi OS and compatible Debian systems.
#
# Environment options:
#   BAMBUCAM_VERSION=1.2.3        install a tagged release
#   BAMBUCAM_BRANCH=main          install a development branch
#   BAMBUCAM_DIR=/opt/bambucam    choose the application directory
#   MEDIAMTX_VERSION=v1.9.3       override the pinned MediaMTX version

set -Eeuo pipefail
umask 027

BAMBUCAM_REPO="fgrfn/bambucam"
BAMBUCAM_DIR="${BAMBUCAM_DIR:-/opt/bambucam}"
BAMBUCAM_CONFIG_DIR="/etc/bambucam"
BAMBUCAM_DATA_DIR="/var/lib/bambucam"
SERVICE_USER="bambucam"
MEDIAMTX_VERSION="${MEDIAMTX_VERSION:-v1.9.3}"
GITHUB_API="https://api.github.com/repos/${BAMBUCAM_REPO}"

if [[ "$BAMBUCAM_DIR" != /* ]]; then
  echo "BAMBUCAM_DIR must be an absolute path" >&2
  exit 1
fi
if [[ "$BAMBUCAM_DIR" =~ [[:space:]] ]]; then
  echo "BAMBUCAM_DIR must not contain whitespace" >&2
  exit 1
fi

_TMP_DIRS=()
cleanup() {
  local directory
  for directory in "${_TMP_DIRS[@]:-}"; do
    [[ -n "$directory" ]] && rm -rf -- "$directory"
  done
}
trap cleanup EXIT

make_temp_dir() {
  local directory
  directory=$(mktemp -d)
  _TMP_DIRS+=("$directory")
  printf '%s\n' "$directory"
}

if [[ -t 1 ]]; then
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
  echo " | __ )  __ _ _ __ ___| |__  _   _/ ___|__ _ _ __ ___"
  echo " |  _ \\ / _\` | '_ \` _ \\| '_ \\| | | | |   / _\` | '_ \` _ \\"
  echo " | |_) | (_| | | | | | | |_) | |_| | |__| (_| | | | | | |"
  echo " |____/ \\__,_|_| |_| |_|_.__/ \\__,_|\\____\\__,_|_| |_| |_|"
  echo -e "${NC}"
  echo "  Raspberry Pi Camera Streaming for BambuBuddy"
  echo "  https://github.com/${BAMBUCAM_REPO}"
}

curl_download() {
  local url=$1
  local destination=$2
  curl --fail --silent --show-error --location \
    --retry 3 --retry-delay 2 --connect-timeout 15 --max-time 300 \
    "$url" --output "$destination"
}

verify_sha256_asset() {
  local asset_path=$1
  local checksums_path=$2
  local filename expected actual
  filename=$(basename "$asset_path")
  expected=$(awk -v name="$filename" '
    $2 == name || $2 == "*" name { print tolower($1); exit }
  ' "$checksums_path")
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || \
    error "SHA256SUMS does not contain a valid checksum for ${filename}"
  actual=$(sha256sum "$asset_path" | awk '{print tolower($1)}')
  [[ "$actual" == "$expected" ]] || error "Checksum verification failed for ${filename}"
  info "Verified SHA-256: ${filename}"
}

safe_extract_tar() {
  local archive=$1
  local destination=$2
  local strip_components=${3:-0}
  local listing
  listing=$(tar -tzf "$archive") || error "Invalid tar archive: $archive"
  if grep -Eq '(^/|(^|/)\.\.(/|$))' <<<"$listing"; then
    error "Unsafe path found in archive: $archive"
  fi
  mkdir -p "$destination"
  tar --extract --gzip --file "$archive" --directory "$destination" \
    --no-same-owner --no-same-permissions --strip-components="$strip_components"
}

banner
step "Checking requirements"
[[ $EUID -eq 0 ]] || error "Run this installer as root (for example through sudo)."
command -v apt-get >/dev/null 2>&1 || error "apt-get not found — a Debian-based OS is required."

step "Installing system packages"
apt-get update -qq
apt-get install -y --no-install-recommends \
  ca-certificates curl git gcc \
  python3 python3-dev python3-pip python3-venv python3-opencv \
  ffmpeg v4l-utils

_RPI_PACKAGES=(python3-picamera2 libcamera-apps)
_MISSING_PACKAGES=()
for package in "${_RPI_PACKAGES[@]}"; do
  if apt-cache show "$package" >/dev/null 2>&1; then
    apt-get install -y --no-install-recommends "$package" -qq || true
  else
    _MISSING_PACKAGES+=("$package")
  fi
done
if (( ${#_MISSING_PACKAGES[@]} > 0 )); then
  warn "RPi camera packages unavailable: ${_MISSING_PACKAGES[*]}"
  warn "CSI cameras may not work; V4L2 USB webcams remain supported."
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-install.sh}")" 2>/dev/null && pwd || true)"
SOURCE_ROOT="$(dirname "$SCRIPT_DIR" 2>/dev/null || true)"
LOCAL_SOURCE=false
if [[ -f "$SOURCE_ROOT/pyproject.toml" ]]; then
  LOCAL_SOURCE=true
  info "Local source tree detected at $SOURCE_ROOT"
fi

INSTALL_TAG=""
INSTALL_BRANCH=""
if [[ "$LOCAL_SOURCE" == "false" ]]; then
  if [[ -n "${BAMBUCAM_VERSION:-}" ]]; then
    INSTALL_TAG="v${BAMBUCAM_VERSION#v}"
  elif [[ -n "${BAMBUCAM_BRANCH:-}" ]]; then
    INSTALL_BRANCH="$BAMBUCAM_BRANCH"
  else
    step "Resolving the latest BambuCam release"
    LATEST_JSON=$(curl --fail --silent --show-error --location \
      --connect-timeout 15 --max-time 60 "${GITHUB_API}/releases/latest" || true)
    INSTALL_TAG=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tag_name", ""))' \
      <<<"$LATEST_JSON" 2>/dev/null || true)
    if [[ -z "$INSTALL_TAG" ]]; then
      warn "No release could be resolved; falling back to the main branch."
      INSTALL_BRANCH="main"
    fi
  fi
fi

SRC_DIR="$SOURCE_ROOT"
WHEEL_FILE=""
if [[ "$LOCAL_SOURCE" == "false" ]]; then
  DOWNLOAD_DIR=$(make_temp_dir)
  SRC_DIR="$DOWNLOAD_DIR/source"
  mkdir -p "$SRC_DIR"

  if [[ -n "$INSTALL_TAG" ]]; then
    BAMBUCAM_VERSION="${INSTALL_TAG#v}"
    RELEASE_BASE="https://github.com/${BAMBUCAM_REPO}/releases/download/${INSTALL_TAG}"
    CHECKSUM_FILE="$DOWNLOAD_DIR/SHA256SUMS"
    BUNDLE_NAME="bambucam-${BAMBUCAM_VERSION}-bundle.tar.gz"
    BUNDLE_FILE="$DOWNLOAD_DIR/$BUNDLE_NAME"
    WHEEL_NAME="bambucam-${BAMBUCAM_VERSION}-py3-none-any.whl"

    step "Downloading verified BambuCam ${INSTALL_TAG} assets"
    if curl_download "$RELEASE_BASE/SHA256SUMS" "$CHECKSUM_FILE" && \
       curl_download "$RELEASE_BASE/$BUNDLE_NAME" "$BUNDLE_FILE"; then
      verify_sha256_asset "$BUNDLE_FILE" "$CHECKSUM_FILE"
      safe_extract_tar "$BUNDLE_FILE" "$SRC_DIR" 1

      if curl_download "$RELEASE_BASE/$WHEEL_NAME" "$DOWNLOAD_DIR/$WHEEL_NAME"; then
        verify_sha256_asset "$DOWNLOAD_DIR/$WHEEL_NAME" "$CHECKSUM_FILE"
        WHEEL_FILE="$DOWNLOAD_DIR/$WHEEL_NAME"
      else
        warn "Release wheel unavailable; installing the verified source bundle."
      fi
    else
      warn "This older release has no verified bundle; using the GitHub source archive."
      SOURCE_ARCHIVE="$DOWNLOAD_DIR/source.tar.gz"
      curl_download "${GITHUB_API}/tarball/${INSTALL_TAG}" "$SOURCE_ARCHIVE"
      safe_extract_tar "$SOURCE_ARCHIVE" "$SRC_DIR" 1
    fi
  else
    INSTALL_BRANCH="${INSTALL_BRANCH:-main}"
    step "Downloading development branch ${INSTALL_BRANCH}"
    SOURCE_ARCHIVE="$DOWNLOAD_DIR/source.tar.gz"
    curl_download "${GITHUB_API}/tarball/${INSTALL_BRANCH}" "$SOURCE_ARCHIVE"
    safe_extract_tar "$SOURCE_ARCHIVE" "$SRC_DIR" 1
    warn "Development branch installations are not release-checksummed."
  fi
fi

[[ -f "$SRC_DIR/pyproject.toml" ]] || error "Downloaded source does not contain pyproject.toml"
[[ -f "$SRC_DIR/config/bambucam.yaml" ]] || error "Downloaded source lacks the default config"
[[ -f "$SRC_DIR/systemd/bambucam.service" ]] || error "Downloaded source lacks the service unit"

ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m)
case "$ARCH" in
  armhf|armv7*)  MEDIAMTX_ARCH="linux_armv7" ;;
  arm64|aarch64) MEDIAMTX_ARCH="linux_arm64v8" ;;
  amd64|x86_64)  MEDIAMTX_ARCH="linux_amd64" ;;
  *) error "Unsupported architecture: $ARCH" ;;
esac
MEDIAMTX_URL="https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_${MEDIAMTX_ARCH}.tar.gz"

step "Installing MediaMTX ${MEDIAMTX_VERSION}"
MEDIAMTX_TMP=$(make_temp_dir)
curl_download "$MEDIAMTX_URL" "$MEDIAMTX_TMP/mediamtx.tar.gz"
safe_extract_tar "$MEDIAMTX_TMP/mediamtx.tar.gz" "$MEDIAMTX_TMP/unpacked"
[[ -x "$MEDIAMTX_TMP/unpacked/mediamtx" ]] || error "MediaMTX archive lacks its executable"
install -m 755 "$MEDIAMTX_TMP/unpacked/mediamtx" /usr/local/bin/mediamtx

step "Creating service account and directories"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi
for group in video gpio i2c; do
  getent group "$group" >/dev/null 2>&1 && usermod -aG "$group" "$SERVICE_USER" || true
done

install -d -m 755 "$BAMBUCAM_DIR" "$BAMBUCAM_CONFIG_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 750 \
  "$BAMBUCAM_DATA_DIR" "$BAMBUCAM_DATA_DIR/snapshots"

step "Installing the BambuCam Python package"
if [[ ! -d "$BAMBUCAM_DIR/venv" ]]; then
  python3 -m venv --system-site-packages "$BAMBUCAM_DIR/venv"
fi
PIP="$BAMBUCAM_DIR/venv/bin/pip"
"$PIP" install --quiet --upgrade pip
if [[ -n "$WHEEL_FILE" ]]; then
  "$PIP" install --quiet --upgrade --no-user "$WHEEL_FILE"
else
  "$PIP" install --quiet --upgrade --no-user "$SRC_DIR"
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$BAMBUCAM_DIR/venv"
"$BAMBUCAM_DIR/venv/bin/bambucam" --version >/dev/null || error "Installed CLI failed"

step "Installing configuration"
if [[ ! -f "$BAMBUCAM_CONFIG_DIR/bambucam.yaml" ]]; then
  install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 640 \
    "$SRC_DIR/config/bambucam.yaml" "$BAMBUCAM_CONFIG_DIR/bambucam.yaml"
else
  chown "$SERVICE_USER:$SERVICE_USER" "$BAMBUCAM_CONFIG_DIR/bambucam.yaml"
  chmod 640 "$BAMBUCAM_CONFIG_DIR/bambucam.yaml"
  info "Existing config preserved: $BAMBUCAM_CONFIG_DIR/bambucam.yaml"
fi
if [[ ! -f "$BAMBUCAM_CONFIG_DIR/environment" ]]; then
  install -o root -g "$SERVICE_USER" -m 640 /dev/null \
    "$BAMBUCAM_CONFIG_DIR/environment"
fi

step "Installing the systemd service"
ESCAPED_DIR=$(printf '%s' "$BAMBUCAM_DIR" | sed 's/[&|]/\\&/g')
sed "s|/opt/bambucam|${ESCAPED_DIR}|g" "$SRC_DIR/systemd/bambucam.service" \
  > /etc/systemd/system/bambucam.service
chmod 644 /etc/systemd/system/bambucam.service

SYSTEMD_OK=false
if command -v systemctl >/dev/null 2>&1 && systemctl --version >/dev/null 2>&1; then
  systemctl daemon-reload
  systemctl enable bambucam.service
  SYSTEMD_OK=true
  if systemctl is-active --quiet bambucam.service; then
    systemctl restart bambucam.service
    info "Running service restarted with the new installation."
  fi
else
  warn "systemd unavailable; start BambuCam manually with:"
  warn "sudo -u $SERVICE_USER $BAMBUCAM_DIR/venv/bin/bambucam"
fi

if command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_camera 0 2>/dev/null || true
fi

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  step "Configuring the firewall"
  ufw allow 8080/tcp comment "BambuCam WebUI+MJPEG" 2>/dev/null || true
  ufw allow 8554/tcp comment "BambuCam RTSP" 2>/dev/null || true
  ufw allow 8888/tcp comment "BambuCam HLS" 2>/dev/null || true
fi

LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
LOCAL_IP="${LOCAL_IP:-<pi-ip>}"

echo
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  BambuCam installation complete${NC}"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if [[ "$SYSTEMD_OK" == "true" ]]; then
  echo -e "  ${BOLD}Start:${NC}     sudo systemctl start bambucam"
  echo -e "  ${BOLD}Logs:${NC}      journalctl -u bambucam -f"
else
  echo -e "  ${BOLD}Start:${NC}     sudo -u $SERVICE_USER $BAMBUCAM_DIR/venv/bin/bambucam"
fi
echo -e "  ${BOLD}WebUI:${NC}     ${CYAN}http://${LOCAL_IP}:8080${NC}"
echo -e "  ${BOLD}RTSP:${NC}      ${CYAN}rtsp://${LOCAL_IP}:8554/cam${NC}"
echo -e "  ${BOLD}Config:${NC}    $BAMBUCAM_CONFIG_DIR/bambucam.yaml"
echo -e "  ${BOLD}Install:${NC}   $BAMBUCAM_DIR"
echo
