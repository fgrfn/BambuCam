# BambuCam

**Raspberry Pi camera streaming software for BambuBuddy and BambuStudio**

BambuCam turns your Raspberry Pi into a dedicated camera server for your Bambu Lab 3D printer.  
It streams the camera feed via **RTSP**, **MJPEG**, and **HLS**, and provides a **WebUI** to configure everything — resolution, framerate, image settings, and more.

---

## Features

| Feature | Status |
|---|---|
| Auto-detect CSI cameras (libcamera) | ✅ |
| USB webcam support (V4L2) | ✅ |
| MJPEG HTTP stream (browser, VLC, OBS) | ✅ |
| RTSP stream (BambuBuddy / BambuStudio) | ✅ |
| HLS stream (browser-native) | ✅ |
| WebUI with live preview | ✅ |
| REST API | ✅ |
| Camera-model-aware settings | ✅ |
| Autofocus (Camera Module 3) | ✅ |
| HDR (Camera Module 3) | ✅ |
| Snapshot endpoint | ✅ |
| systemd service | ✅ |
| One-line installer | ✅ |

---

## Supported Hardware

### Raspberry Pi Models
- Raspberry Pi 2 / 3 / 3B+
- Raspberry Pi 4 (recommended)
- Raspberry Pi 5

### Camera Modules

| Module | Sensor | Resolution | FPS | Autofocus | HDR |
|---|---|---|---|---|---|
| Camera Module v1 | OV5647 | 2592×1944 | 90 | — | — |
| Camera Module v2 | IMX219 | 3280×2464 | 90 | — | — |
| Camera Module v2 NoIR | IMX219 | 3280×2464 | 90 | — | — |
| **Camera Module 3** | IMX708 | 4608×2592 | 120 | ✅ PDAF | ✅ |
| Camera Module 3 Wide | IMX708 | 4608×2592 | 120 | ✅ | ✅ |
| HQ Camera | IMX477 | 4056×3040 | 120 | — | — |
| Global Shutter Camera | IMX296 | 1456×1088 | 60 | — | — |
| USB Webcam | any | up to 1080p | 30 | — | — |

---

## Quick Start

### 1. Install (Raspberry Pi OS Bullseye / Bookworm)

```bash
git clone https://github.com/fgrfn/bambucam
cd bambucam
sudo bash scripts/install.sh
sudo systemctl start bambucam
```

### 2. Access WebUI

Open `http://<your-pi-ip>:8080` in your browser.

### 3. Configure BambuBuddy

In BambuBuddy go to **Settings → Camera → Custom RTSP URL** and enter:

```
rtsp://<your-pi-ip>:8554/cam
```

Or use the **BambuCam WebUI** — it shows you the exact URL on the main page.

---

## Stream URLs

| Protocol | URL | Use case |
|---|---|---|
| RTSP | `rtsp://<pi-ip>:8554/cam` | BambuBuddy, VLC, ffplay |
| MJPEG | `http://<pi-ip>:8080/stream` | Browser, OBS |
| HLS | `http://<pi-ip>:8888/cam/index.m3u8` | Browser (native) |
| Snapshot | `http://<pi-ip>:8080/snapshot` | Single JPEG frame |

---

## REST API

All endpoints under `/api/v1/`:

```
GET  /camera/status          Camera state & current settings
GET  /camera/models          All supported camera models & capabilities
GET  /camera/detect          Scan for connected cameras
POST /camera/settings        Apply camera settings (JSON body)

GET  /stream/status          Stream URLs & client count
POST /stream/rtsp/start      Start RTSP streamer
POST /stream/rtsp/stop       Stop RTSP streamer
POST /stream/rtsp/settings   Update RTSP settings

GET  /snapshot               Capture JPEG snapshot
GET  /snapshot?save=true     Capture and save to disk
GET  /snapshot/list          List saved snapshots

GET  /config                 Full configuration (passwords redacted)
POST /config                 Update configuration

GET  /system                 CPU temp, memory, disk, uptime

GET  /bambubuddy             BambuBuddy integration URLs & instructions
```

### Example: change resolution

```bash
curl -X POST http://<pi-ip>:8080/api/v1/camera/settings \
  -H 'Content-Type: application/json' \
  -d '{"resolution": "1280x720", "framerate": 30}'
```

---

## Configuration

Edit `/etc/bambucam/bambucam.yaml` (or `~/.config/bambucam/bambucam.yaml` for user installs):

```yaml
camera:
  resolution: 1920x1080
  framerate: 15
  vflip: false
  hflip: false
  autofocus: true      # Camera Module 3 only
  hdr: false           # Camera Module 3 only

streaming:
  rtsp:
    enabled: true
    bitrate_kbps: 2000
  mjpeg:
    fps: 15

web:
  port: 8080
  auth:
    enabled: false     # Set to true + password to protect WebUI
```

Full reference: [docs/configuration.md](docs/configuration.md)

---

## Architecture

```
Camera Hardware
    │
    ▼
┌─────────────────────────────────┐
│         Camera Backend          │
│  picamera2 (CSI) / V4L2 (USB)  │
└────────────┬────────────────────┘
             │ JPEG frames
     ┌───────┴────────────┐
     │                    │
     ▼                    ▼
┌──────────┐       ┌─────────────────────────────┐
│  MJPEG   │       │  ffmpeg → MediaMTX           │
│  Server  │       │  RTSP / HLS / WebRTC         │
└──────────┘       └─────────────────────────────┘
     │                    │
     ▼                    ▼
HTTP :8080          RTSP :8554
                    HLS  :8888

     ▲
     │
┌──────────────────────┐
│   Flask WebUI + API  │
│   /api/v1/...        │
└──────────────────────┘
```

---

## Planned Features / Ideas

- [ ] **Motion detection** — record on motion, push notifications (Telegram/Discord)
- [ ] **Timelapse** — auto-timelapse of print jobs
- [ ] **OSD / Overlay** — timestamp, printer name, temperature overlay on stream
- [ ] **AI spaghetti detection** — detect failed prints (Obico-style)
- [ ] **Multi-camera** — multiple cameras on one Pi, multiple streams
- [ ] **Circular recording** — keep last N minutes to disk
- [ ] **ONVIF support** — industry-standard camera protocol
- [ ] **IR LED control** — night vision for NoIR cameras
- [ ] **mDNS / Bonjour** — zero-config discovery on local network (`bambucam.local`)
- [ ] **Docker image** — containerized deployment
- [ ] **HTTPS / TLS** — secure connections with Let's Encrypt
- [ ] **WebUI redesign** — full design via Claude Design

---

## Development

```bash
git clone https://github.com/fgrfn/bambucam
cd bambucam
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
pytest tests/
```

List cameras without starting the full server:

```bash
bambucam --list-cameras
```

---

## License

MIT — see [LICENSE](LICENSE)
