"""One-time repository patch for the hardening branch."""

from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8")
    if old not in content:
        raise RuntimeError(f"Expected text not found in {path}: {old[:80]!r}")
    file_path.write_text(content.replace(old, new, 1), encoding="utf-8")


replace(
    "bambucam/main.py",
    '''        mediamtx_path=Path(cfg.system.get("mediamtx_path", "/usr/local/bin/mediamtx")),
        enable_hls=rtsp_config.get("enable_hls", True),
        enable_webrtc=rtsp_config.get("enable_webrtc", False),
''',
    '''        mediamtx_path=Path(cfg.system.get("mediamtx_path", "/usr/local/bin/mediamtx")),
        ffmpeg_path=cfg.system.get("ffmpeg_path", "ffmpeg"),
        rtsp_port=rtsp_config.get("port", 8554),
        hls_port=rtsp_config.get("hls_port", 8888),
        webrtc_port=rtsp_config.get("webrtc_port", 8889),
        enable_hls=rtsp_config.get("enable_hls", True),
        enable_webrtc=rtsp_config.get("enable_webrtc", False),
''',
)
replace(
    "bambucam/main.py",
    '''        rtsp_auth_pass=rtsp_auth.get("password") if rtsp_auth.get("enabled") else None,
        camera_backend=picamera2_backend,
    )
''',
    '''        rtsp_auth_pass=rtsp_auth.get("password") if rtsp_auth.get("enabled") else None,
        camera_backend=picamera2_backend,
        capture_fn=(
            camera.capture_jpeg
            if camera_ok and picamera2_backend is None
            else None
        ),
    )
''',
)
replace(
    "bambucam/main.py",
    '''    log.info("WebUI listening on http://%s:%d", host, port)
    log.info("MJPEG stream: http://<pi-ip>:%d/stream", port)
    if rtsp.is_running:
        log.info("RTSP stream: %s", rtsp.stream_urls("<pi-ip>")["rtsp"])

    try:
        app.run(host=host, port=port, threaded=True, use_reloader=False)
''',
    '''    https_config = web_config.get("https", {})
    ssl_context = None
    scheme = "http"
    if https_config.get("enabled", False):
        cert_path = Path(str(https_config.get("cert", "")))
        key_path = Path(str(https_config.get("key", "")))
        if not cert_path.is_file() or not key_path.is_file():
            raise RuntimeError("HTTPS is enabled but certificate or key file is missing")
        ssl_context = (str(cert_path), str(key_path))
        scheme = "https"

    log.info("WebUI listening on %s://%s:%d", scheme, host, port)
    log.info("MJPEG stream: %s://<pi-ip>:%d/stream", scheme, port)
    if rtsp.is_running:
        log.info("RTSP stream: %s", rtsp.stream_urls("<pi-ip>")["rtsp"])

    try:
        app.run(
            host=host,
            port=port,
            threaded=True,
            use_reloader=False,
            ssl_context=ssl_context,
        )
''',
)

replace(
    "bambucam/config.py",
    '''        "auth": {
            "enabled": False,
            "username": "admin",
            "password": "",
        },
        "https": {
''',
    '''        "auth": {
            "enabled": False,
            "username": "admin",
            "password": "",
            "api_token": "",
        },
        "trust_proxy": False,
        "https": {
''',
)

replace(
    "config/bambucam.yaml",
    '''  auth:
    enabled: false
    username: admin
    password: ""             # Set a strong password here

  https:
''',
    '''  auth:
    enabled: false
    username: admin
    password: ""             # Stored as a secure hash after startup
    api_token: ""            # Optional Bearer token for integrations

  trust_proxy: false          # Enable only behind a trusted reverse proxy

  https:
''',
)

replace(
    "bambucam/web/templates/index.html",
    "const opts = { method, headers: { 'Content-Type': 'application/json' } };",
    "const opts = { method, headers: { 'Content-Type': 'application/json', 'X-BambuCam-CSRF': '1' } };",
)
replace(
    "bambucam/web/templates/index.html",
    '''  el.innerHTML = `<span class="toast-dot"></span><span>${msg}</span>`;
''',
    '''  const dot = document.createElement('span');
  dot.className = 'toast-dot';
  const text = document.createElement('span');
  text.textContent = String(msg);
  el.append(dot, text);
''',
)
replace(
    "bambucam/web/templates/index.html",
    '''    const port    = parseInt(document.getElementById('cfg-mjpeg-port').value);
    const quality = parseInt(document.getElementById('mjpeg-quality').value);
    await api('POST', '/config', { streaming: { mjpeg: { port, quality } } });
''',
    '''    const port    = parseInt(document.getElementById('cfg-mjpeg-port').value);
    const quality = parseInt(document.getElementById('mjpeg-quality').value);
    const fps     = parseInt(document.getElementById('cfg-mjpeg-fps').value);
    await api('POST', '/config', { streaming: { mjpeg: { port, quality, fps } } });
''',
)
