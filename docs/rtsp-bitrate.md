# RTSP H.264 target bitrate limits

All values below are **kbps**. BambuCam uses the lower of the hardware ceiling
and the encoded pixel-area ceiling:

| Hardware tier | Examples | Maximum |
| --- | --- | ---: |
| 1 | Original Zero, Pi 1/2, CM1 | 3000 |
| 2 | Zero 2 W, Pi 3, CM3 | 8000 |
| 3 | Pi 4/5, CM4/5, non-Pi | 20000 |

| Encoded pixel area | Maximum |
| --- | ---: |
| Up to 640 × 480 | 2000 |
| Up to 1280 × 720 | 6000 |
| Up to 1920 × 1080 | 12000 |
| Larger outputs | 20000 |

The minimum is 100. These are practical BambuCam target-bitrate limits, not
measured codec maxima or a guarantee that a device/network sustains every
allowed combination. The same policy covers Picamera2 H264Encoder and ffmpeg
hardware/software publishers. H.264 target bitrate does not cap instantaneous
network traffic; audio, protocol overhead and encoder variation are separate.
FPS continues to be validated separately.

For CSI cameras the actual lores H.264 encode size determines the limit,
independently of the main/snapshot resolution. For example, 2592x1944 main with
1920x1080 H.264 on CM4 allows 100–12000, while a 640x360 encode stream allows
100–2000. Existing hardware-dependent encode-size selection is retained.

`GET /api/v1/stream/status` includes `rtsp.bitrate_limits` (minimum, maximum,
hardware tier, each contributing ceiling and actual encode resolution) and
`bitrate_limits_by_resolution` for the available main resolutions, including
when RTSP is disabled. The WebUI consumes these values, updates its slider for
the selected output and shows the H.264 resolution to which the range applies.

Config/API values must be integers. Out-of-range requests return HTTP 400 with
the allowed range and context before the camera or publisher is stopped. When
reducing resolution, submit the lower bitrate with it using
`POST /api/v1/stream/settings`, for example:

```json
{"resolution":"640x480","framerate":15,"bitrate_kbps":2000}
```

The config loader validates known configured dimensions and hardware; an
`auto` size defers the area limit until startup knows the encode size. On load,
legacy excessive integer bitrates are reduced and saved. Startup also reduces
and persists values above the actual encode-stream limit, logging the change.
New API updates are rejected rather than silently clamped. Profiles resolve
their preset bitrates against the same effective limit.

## Hardware encoder frame-rate limit

Raspberry Pi boards up to the Pi 4 / CM4 encode H.264 on the VideoCore, which
is specified for 1080p30 (the macroblock rate of H.264 level 4.0). When the
H.264 stream is enabled on such a board, each video mode's maximum frame rate
is capped at what the encoder sustains for that mode's encode size, and the
WebUI marks capped modes as "H.264 encoder limit". The Pi 5 / CM5 has no
hardware H.264 encoder and is not capped this way.
