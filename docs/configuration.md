# BambuCam Configuration Reference

## Config file locations

BambuCam loads configuration from (later overrides earlier):

1. Built-in defaults
2. `/etc/bambucam/bambucam.yaml` — system-wide (created by installer)
3. `~/.config/bambucam/bambucam.yaml` — per-user
4. `$BAMBUCAM_CONFIG` environment variable — custom path
5. `--config <path>` CLI argument

## Full annotated example

```yaml
# Camera settings
camera:
  index: 0                   # Which camera to use (0 = first detected)
  backend: auto              # auto | picamera2 | v4l2

  # Resolution and frame rate
  resolution: 1920x1080      # e.g. 3280x2464, 1920x1080, 1280x720, 640x480
  framerate: 15              # 1–120 fps (depends on camera model & resolution)

  # Image adjustments
  brightness: 0.0            # -1.0 (dark) to 1.0 (bright)
  contrast: 1.0              # 0.0 (flat) to 32.0 (high contrast)
  saturation: 1.0            # 0.0 (greyscale) to 32.0
  sharpness: 1.0             # 0.0 (soft) to 16.0

  # Exposure
  exposure_mode: auto        # auto | sport | night

  # White balance
  awb_mode: auto             # auto | sunlight | cloudy | shade | tungsten | fluorescent

  # Orientation
  vflip: false               # Flip vertically
  hflip: false               # Flip horizontally

  # Camera Module 3 specific (no effect on other models)
  autofocus: true            # Enable continuous autofocus
  hdr: false                 # Enable HDR mode

streaming:
  mjpeg:
    enabled: true
    port: 8080               # Same port as WebUI; stream at /stream
    quality: 85              # JPEG compression quality (1–100)
    fps: 15                  # MJPEG stream target framerate

  rtsp:
    enabled: true
    port: 8554               # RTSP clients connect to this port
    stream_name: cam         # Stream path: rtsp://<ip>:<port>/<stream_name>
    bitrate_kbps: 2000       # H.264 encoding bitrate

    # HLS (HTTP Live Streaming)
    enable_hls: true
    hls_port: 8888           # http://<ip>:8888/<stream_name>/index.m3u8

    # WebRTC (experimental, low-latency)
    enable_webrtc: false
    webrtc_port: 8889

    # Authentication (optional — protects RTSP stream)
    auth:
      enabled: false
      username: ""
      password: ""

  snapshot:
    enabled: true
    save_dir: /var/lib/bambucam/snapshots   # Where saved snapshots go

web:
  host: 0.0.0.0              # Listen on all interfaces; use 127.0.0.1 for localhost only
  port: 8080
  secret_key: ""             # Flask session secret (auto-generated if empty)

  # WebUI password protection
  auth:
    enabled: false
    username: admin
    password: ""             # Set a strong password

  # HTTPS (bring your own certificate)
  https:
    enabled: false
    cert: /etc/ssl/bambucam.crt
    key: /etc/ssl/bambucam.key

system:
  log_level: INFO            # DEBUG | INFO | WARNING | ERROR
  mediamtx_path: /usr/local/bin/mediamtx
  ffmpeg_path: ffmpeg        # ffmpeg binary (must be in PATH or absolute path)
```

## Camera resolution guide

### Recommended settings by use case

| Use case | Resolution | FPS | Bitrate |
|---|---|---|---|
| 3D print monitoring (BambuBuddy) | `1920x1080` | `15` | `2000` |
| Low-latency preview | `1280x720` | `30` | `3000` |
| High quality archive | `3280x2464` | `10` | `5000` |
| Low-bandwidth / RPi 2 | `640x480` | `15` | `500` |
| Night vision (NoIR) | `1920x1080` | `15` | `2000` |

## Environment variables

| Variable | Description |
|---|---|
| `BAMBUCAM_CONFIG` | Path to config file |
| `PYTHONUNBUFFERED` | Set to `1` for immediate log output |

## Applying settings at runtime

Settings can also be changed without restarting via the API:

```bash
# Change resolution
curl -X POST http://localhost:8080/api/v1/camera/settings \
  -H 'Content-Type: application/json' \
  -d '{"resolution": "1280x720", "framerate": 30}'

# Flip image
curl -X POST http://localhost:8080/api/v1/camera/settings \
  -H 'Content-Type: application/json' \
  -d '{"vflip": true}'
```

Settings changed via API or WebUI are persisted to `~/.config/bambucam/bambucam.yaml`.
