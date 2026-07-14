# BambuCam Configuration Reference

## Config file locations

BambuCam loads configuration from (later overrides earlier):

1. Built-in defaults
2. `/etc/bambucam/bambucam.yaml` — system-wide (created by installer)
3. `~/.config/bambucam/bambucam.yaml` — per-user
4. `$BAMBUCAM_CONFIG` environment variable — custom path
5. `--config <path>` CLI argument

The service installation persists WebUI changes back to `/etc/bambucam/bambucam.yaml`. A user installation persists to the explicit `--config` file or the per-user path.

## Full annotated example

```yaml
camera:
  index: 0                   # Camera index after backend filtering
  backend: auto              # auto | picamera2 | v4l2
  module: auto               # Optional model override

  # Model- and Raspberry-Pi-aware defaults. Explicit values remain supported.
  resolution: auto           # auto or e.g. 1920x1080
  framerate: auto            # auto or 1–120; capped to supported limits

  brightness: 0.0            # -1.0 (dark) to 1.0 (bright)
  contrast: 1.0              # 0.0 to 32.0
  saturation: 1.0            # 0.0 to 32.0
  sharpness: 1.0             # 0.0 to 16.0

  exposure_mode: auto        # auto | sport | night
  awb_mode: auto             # auto | sunlight | cloudy | shade | tungsten | fluorescent

  vflip: false
  hflip: false

  autofocus: true            # Camera Module 3
  hdr: false                 # Camera Module 3

streaming:
  mjpeg:
    enabled: true
    port: 8080               # Same port as WebUI; stream at /stream
    quality: 85              # JPEG compression quality (1–100)
    fps: 15                  # Target, capped to current camera FPS

  rtsp:
    enabled: true
    port: 8554
    stream_name: cam
    bitrate_kbps: 2000

    enable_hls: true
    hls_port: 8888

    enable_webrtc: false
    webrtc_port: 8889

    auth:
      enabled: false
      username: ""
      password: ""

  snapshot:
    enabled: true
    save_dir: /var/lib/bambucam/snapshots
    max_count: 500
    max_age_days: 30
    max_bytes: 1073741824

web:
  host: 0.0.0.0
  port: 8080
  secret_key: ""

  auth:
    enabled: false
    username: admin
    password: ""

  https:
    enabled: false
    cert: /etc/ssl/bambucam.crt
    key: /etc/ssl/bambucam.key

system:
  log_level: INFO
  mediamtx_path: /usr/local/bin/mediamtx
  ffmpeg_path: ffmpeg
  diagnostics_log_lines: 300
```

## Automatic camera modes

With `resolution: auto` and `framerate: auto`, BambuCam selects a mode from the detected camera capabilities and applies the Raspberry Pi hardware-tier FPS cap. For generic USB webcams, the largest mode actually reported by V4L2 is preferred instead of assuming a Raspberry Pi camera mode.

Explicit settings are validated before the backend starts. Unsupported resolutions are rejected and excessive frame rates are capped to the effective model and hardware limit.

## Camera resolution guide

| Use case | Resolution | FPS | Bitrate |
|---|---|---|---|
| Automatic/default | `auto` | `auto` | `2000` |
| 3D print monitoring | `1920x1080` | `15` | `2000` |
| Low-latency preview | `1280x720` | `30` | `3000` |
| High quality archive | `3280x2464` | `10` | `5000` |
| Low-bandwidth / RPi 2 | `640x480` | `15` | `500` |

## Environment variables

| Variable | Description |
|---|---|
| `BAMBUCAM_CONFIG` | Path to config file |
| `PYTHONUNBUFFERED` | Set to `1` for immediate log output |

## Applying settings at runtime

```bash
curl -X POST http://localhost:8080/api/v1/camera/settings \
  -H 'Content-Type: application/json' \
  -d '{"resolution": "1280x720", "framerate": 30}'

curl -X POST http://localhost:8080/api/v1/camera/settings \
  -H 'Content-Type: application/json' \
  -d '{"vflip": true}'
```

Runtime changes are persisted atomically, so an interrupted write cannot leave a partially written YAML file.
