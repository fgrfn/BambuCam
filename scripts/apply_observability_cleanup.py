"""One-time source transformation for the observability/UI cleanup branch."""

import re
from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8")
    if old not in content:
        raise RuntimeError(f"Expected text not found in {path}: {old[:100]!r}")
    file_path.write_text(content.replace(old, new, 1), encoding="utf-8")


replace(
    "bambucam/config.py",
    '''        "snapshot": {
            "enabled": True,
            "path": "/snapshot",
            "save_dir": "/var/lib/bambucam/snapshots",
        },
''',
    '''        "snapshot": {
            "enabled": True,
            "path": "/snapshot",
            "save_dir": "/var/lib/bambucam/snapshots",
            "max_count": 500,
            "max_age_days": 30,
            "max_bytes": 1073741824,
        },
''',
)
replace(
    "bambucam/config.py",
    '''        "ffmpeg_path": "ffmpeg",
    },
''',
    '''        "ffmpeg_path": "ffmpeg",
        "diagnostics_log_lines": 300,
    },
''',
)

replace(
    "config/bambucam.yaml",
    '''  snapshot:
    enabled: true
    save_dir: /var/lib/bambucam/snapshots
''',
    '''  snapshot:
    enabled: true
    save_dir: /var/lib/bambucam/snapshots
    max_count: 500            # 0 disables the count limit
    max_age_days: 30          # 0 disables the age limit
    max_bytes: 1073741824     # 1 GiB; 0 disables the byte limit
''',
)
replace(
    "config/bambucam.yaml",
    '''  ffmpeg_path: ffmpeg
''',
    '''  ffmpeg_path: ffmpeg
  diagnostics_log_lines: 300  # Recent in-memory log lines in support bundles
''',
)

replace(
    "bambucam/main.py",
    '''    snapshot = SnapshotService(
        capture_fn=(lambda: camera.capture_jpeg(quality=95)) if camera_ok else lambda: None,
        snapshot_dir=Path(
            streaming_config.get("snapshot", {}).get("save_dir", "/var/lib/bambucam/snapshots")
        ),
    )
''',
    '''    snapshot_config = streaming_config.get("snapshot", {})
    snapshot = SnapshotService(
        capture_fn=(lambda: camera.capture_jpeg(quality=95)) if camera_ok else lambda: None,
        snapshot_dir=Path(
            snapshot_config.get("save_dir", "/var/lib/bambucam/snapshots")
        ),
        max_count=snapshot_config.get("max_count", 500),
        max_age_days=snapshot_config.get("max_age_days", 30),
        max_bytes=snapshot_config.get("max_bytes", 1024 * 1024 * 1024),
    )
''',
)

replace(
    "docs/configuration.md",
    '''  snapshot:
    enabled: true
    save_dir: /var/lib/bambucam/snapshots
''',
    '''  snapshot:
    enabled: true
    save_dir: /var/lib/bambucam/snapshots
    max_count: 500
    max_age_days: 30
    max_bytes: 1073741824
''',
)
replace(
    "docs/configuration.md",
    '''  ffmpeg_path: ffmpeg
''',
    '''  ffmpeg_path: ffmpeg
  diagnostics_log_lines: 300
''',
)

readme = Path("README.md")
readme_text = readme.read_text(encoding="utf-8")
marker = "## Updates\n"
observability_section = '''## Monitoring and diagnostics

- `GET /health` is a public readiness endpoint and returns HTTP 503 when the camera/stream stack is degraded.
- `GET /metrics` exposes Prometheus text metrics and follows normal WebUI authentication.
- `GET /api/v1/diagnostics` returns a credential-redacted support payload.
- `GET /api/v1/diagnostics/download` downloads redacted JSON and recent in-memory logs as a ZIP.
- Snapshot retention supports count, age, and total-byte limits and can be updated through `/api/v1/snapshot/retention`.

'''
if observability_section not in readme_text:
    if marker not in readme_text:
        raise RuntimeError("README Updates marker not found")
    readme_text = readme_text.replace(marker, observability_section + marker, 1)
readme.write_text(readme_text, encoding="utf-8")

# Split the single-file WebUI into a compact Jinja template plus packaged static assets.
template_path = Path("bambucam/web/templates/index.html")
template = template_path.read_text(encoding="utf-8")
style_match = re.search(r"\n\s*<style>\s*(.*?)\s*</style>\s*", template, flags=re.DOTALL)
script_matches = list(re.finditer(r"\n\s*<script>\s*(.*?)\s*</script>\s*", template, flags=re.DOTALL))
if style_match is None or not script_matches:
    raise RuntimeError("Expected inline WebUI style/script blocks were not found")
script_match = script_matches[-1]

css = style_match.group(1).strip() + "\n"
javascript = script_match.group(1).strip() + "\n"
css_path = Path("bambucam/web/static/css/app.css")
js_path = Path("bambucam/web/static/js/app.js")
css_path.parent.mkdir(parents=True, exist_ok=True)
js_path.parent.mkdir(parents=True, exist_ok=True)
css_path.write_text(css, encoding="utf-8")
js_path.write_text(javascript, encoding="utf-8")

template = template[: style_match.start()] + (
    '\n  <link rel="stylesheet" href="{{ url_for(\'static\', filename=\'css/app.css\') }}" />\n'
) + template[style_match.end() :]
# Re-find the script after the style replacement to avoid stale offsets.
script_matches = list(re.finditer(r"\n\s*<script>\s*(.*?)\s*</script>\s*", template, flags=re.DOTALL))
script_match = script_matches[-1]
template = template[: script_match.start()] + (
    '\n<script src="{{ url_for(\'static\', filename=\'js/app.js\') }}" defer></script>\n'
) + template[script_match.end() :]

footer_old = '''<footer>
  <span>BambuCam — Open Source Raspberry Pi Camera Streaming</span>
  <a href="https://github.com/fgrfn/bambucam" target="_blank" rel="noopener">GitHub ↗</a>
</footer>'''
footer_new = '''<footer>
  <span>BambuCam — Open Source Raspberry Pi Camera Streaming</span>
  <span>
    <a href="/metrics" target="_blank" rel="noopener">Metrics ↗</a>
    ·
    <a href="/api/v1/diagnostics/download">Diagnosepaket ↓</a>
    ·
    <a href="https://github.com/fgrfn/bambucam" target="_blank" rel="noopener">GitHub ↗</a>
  </span>
</footer>'''
if footer_old in template:
    template = template.replace(footer_old, footer_new, 1)
else:
    raise RuntimeError("Expected WebUI footer was not found")
template_path.write_text(template, encoding="utf-8")
