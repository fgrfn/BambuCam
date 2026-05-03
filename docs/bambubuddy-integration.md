# BambuBuddy / BambuStudio Integration

## Overview

BambuCam streams your camera feed via **RTSP** — the protocol that both
BambuBuddy and BambuStudio use for custom camera integration.

## Step-by-step setup

### 1. Install BambuCam on your Raspberry Pi

```bash
sudo bash scripts/install.sh
sudo systemctl start bambucam
```

### 2. Find your RTSP URL

Open the BambuCam WebUI in your browser:

```
http://<your-pi-ip>:8080
```

The main page shows the RTSP URL with your Pi's actual IP address.  
It looks like: `rtsp://192.168.1.42:8554/cam`

You can also query the API directly:

```bash
curl http://<pi-ip>:8080/api/v1/bambubuddy
```

### 3. Configure BambuBuddy

1. Open BambuBuddy
2. Go to **Settings** → **Camera**
3. Select **Custom RTSP URL**
4. Paste your RTSP URL: `rtsp://<pi-ip>:8554/cam`
5. Save and reload

### 4. Configure BambuStudio

1. Open BambuStudio
2. Go to **Printer Settings** → **Camera**
3. Enter the RTSP URL

## Recommended camera settings for BambuBuddy

```yaml
camera:
  resolution: 1920x1080    # Full HD — good balance
  framerate: 15            # 15 fps is sufficient for print monitoring
  exposure_mode: auto
  awb_mode: auto

streaming:
  rtsp:
    bitrate_kbps: 2000     # 2 Mbit/s — good quality, low CPU
    stream_name: cam
```

## Troubleshooting

### BambuBuddy shows blank / no stream

1. Check BambuCam is running: `systemctl status bambucam`
2. Test RTSP directly with VLC: Open → Network → paste RTSP URL
3. Check the port is reachable: `nc -zv <pi-ip> 8554`
4. Check firewall: `sudo ufw status` — port 8554 must be allowed

### High latency in BambuBuddy

Lower the bitrate and ensure H.264 is used (not MJPEG):

```yaml
streaming:
  rtsp:
    bitrate_kbps: 1000
```

### Stream cuts out

- Check CPU temperature: `bambucam --system` or the WebUI system panel  
- Reduce resolution or framerate  
- Add a heatsink / fan to your Pi

### Authentication

If you enable RTSP auth in `bambucam.yaml`:

```yaml
streaming:
  rtsp:
    auth:
      enabled: true
      username: bambucam
      password: mypassword
```

Then use: `rtsp://bambucam:mypassword@<pi-ip>:8554/cam`

## Network requirements

BambuBuddy and your Pi must be on the **same local network** (or reachable via VPN).  
The RTSP stream does not go through the internet.

Required open ports (local network only):

| Port | Protocol | Purpose |
|---|---|---|
| 8080 | TCP | WebUI + MJPEG stream |
| 8554 | TCP | RTSP (BambuBuddy) |
| 8888 | TCP | HLS (optional) |
