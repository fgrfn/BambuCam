'use strict';

// ── Module-level camera model state ───────────────────────────────────────
let resolutionMaxFps = {};  // populated on load from camera model data

// ── API helpers ────────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json', 'X-BambuCam-CSRF': '1' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch('/api/v1' + path, opts);
  if (!r.ok) {
    const err = await r.json().catch(() => ({ error: r.statusText }));
    throw new Error(err.error || r.statusText);
  }
  return r.json();
}

// ── Toast ──────────────────────────────────────────────────────────────────
function toast(msg, type = 'success') {
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  const dot = document.createElement('span');
  dot.className = 'toast-dot';
  const text = document.createElement('span');
  text.textContent = String(msg);
  el.append(dot, text);
  const container = document.getElementById('toast-container');
  container.appendChild(el);
  setTimeout(() => {
    el.classList.add('out');
    setTimeout(() => el.remove(), 250);
  }, 3000);
}

// ── Button loading state ───────────────────────────────────────────────────
function btnLoading(id, loading, label) {
  const btn = document.getElementById(id);
  if (!btn) return;
  btn.disabled = loading;
  if (loading) {
    btn.dataset.origLabel = btn.innerHTML;
    btn.innerHTML = `<span class="spinner"></span> ${label || 'Bitte warten…'}`;
  } else {
    if (btn.dataset.origLabel) btn.innerHTML = btn.dataset.origLabel;
  }
}

// ── Copy helpers ───────────────────────────────────────────────────────────
function copyText(text, el) {
  function flash() {
    if (el) {
      el.classList.add('copied');
      setTimeout(() => el.classList.remove('copied'), 800);
    }
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(flash);
  } else {
    // Fallback for plain HTTP (clipboard API requires HTTPS/localhost)
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (_) {}
    document.body.removeChild(ta);
    flash();
  }
}

function copyChip(el) {
  const url = el.querySelector('.url-chip-url').textContent;
  copyText(url, el);
}

function copyBox(el) {
  const url = el.querySelector('.copy-box-url').textContent;
  copyText(url, el);
}

// ── Format helpers ─────────────────────────────────────────────────────────
function fmtUptime(sec) {
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

// ── Init & polling ─────────────────────────────────────────────────────────
async function init() {
  await Promise.allSettled([
    loadCameraStatus(),
    loadStreamStatus(),
    loadSystemInfo(),
    loadNetworkConfig(),
    loadSnapshotList(),
  ]);
  loadInitialUpdateStatus();

  setInterval(loadCameraStatus, 5000);
  setInterval(loadSystemInfo, 10000);
  setInterval(loadStreamStatus, 8000);
}

// ── Camera status ──────────────────────────────────────────────────────────
async function loadCameraStatus() {
  try {
    const s = await api('GET', '/camera/status');

    setText('cam-model',   s.model   || '—');
    setText('cam-sensor',  s.sensor  || '—');
    setText('cam-backend', s.backend || '—');
    setText('cam-res',     s.resolution || '—');
    setText('cam-fps',     s.framerate ? s.framerate + ' fps' : '—');

    const dot  = document.getElementById('status-dot');
    const txt  = document.getElementById('status-text');
    dot.classList.remove('pulse');
    if (s.running) {
      dot.className = 'status-dot ok';
      txt.textContent = 'Kamera aktiv';
    } else {
      dot.className = 'status-dot error';
      txt.textContent = 'Kamera gestoppt';
    }

    // Conditional controls
    toggleRow('row-autofocus', s.has_autofocus);
    toggleRow('row-hdr',       s.has_hdr);

    await loadModelCapabilities(s);
  } catch(e) {
    const dot = document.getElementById('status-dot');
    dot.className = 'status-dot error';
    document.getElementById('status-text').textContent = 'Verbindungsfehler';
  }
}

async function loadModelCapabilities(status) {
  if (!status.model) return;
  try {
    const models = await api('GET', '/camera/models');
    const model = models.find(m => m.name === status.model);
    if (!model) return;

    const currentRes = status.resolution || '';
    const currentFps = status.framerate || 15;
    const resSel = document.getElementById('sel-resolution');
    const fpsSel = document.getElementById('sel-framerate');

    function buildFpsOptions(res, selectedFps) {
      const maxFps = (model.resolution_max_framerates || {})[res] ?? model.max_framerate;
      fpsSel.innerHTML = model.supported_framerates
        .filter(f => f <= maxFps)
        .map(f => `<option value="${f}"${f == selectedFps ? ' selected' : ''}>${f} fps</option>`)
        .join('');
    }

    const resFpsMap = model.resolution_max_framerates || {};
    resSel.innerHTML = model.supported_resolutions.map(r => {
      const maxFps = resFpsMap[r];
      const label = maxFps ? `${r} (max ${maxFps} fps)` : r;
      return `<option value="${r}"${r === currentRes ? ' selected' : ''}>${label}</option>`;
    }).join('');

    resolutionMaxFps = model.resolution_max_framerates || {};

    buildFpsOptions(currentRes, currentFps);

    resSel.addEventListener('change', () => {
      const selectedRes = resSel.value;
      const maxFps = resolutionMaxFps[selectedRes] ?? model.max_framerate;
      buildFpsOptions(selectedRes, maxFps);
    });
  } catch(e) { /* ignore */ }
}

// ── Stream status ──────────────────────────────────────────────────────────
async function loadStreamStatus() {
  try {
    const s = await api('GET', '/stream/status');

    if (s.mjpeg) {
      setText('chip-mjpeg-url', s.mjpeg.url || 'http://…/stream');

      // Actual measured fps
      const actualFps = s.mjpeg.actual_fps;
      const fpsEl = document.getElementById('cam-fps-actual');
      if (fpsEl) fpsEl.textContent = actualFps != null ? ' (' + actualFps + ' real)' : '';

      const cnt = s.mjpeg.clients || 0;
      const vb = document.getElementById('viewers-badge');
      if (cnt > 0) {
        vb.classList.remove('hidden');
        setText('viewers-count', cnt);
      } else {
        vb.classList.add('hidden');
      }
    }

    const rtspRunning = !!(s.rtsp && s.rtsp.running);

    // Sync RTSP toggle (only when not mid-toggle)
    const rtspToggle = document.getElementById('chk-rtsp');
    if (rtspToggle && !rtspToggle._busy) rtspToggle.checked = rtspRunning;

    // Show/hide RTSP + HLS chips
    const chipRtsp = document.getElementById('chip-rtsp');
    const chipHls  = document.getElementById('chip-hls');
    if (chipRtsp) chipRtsp.style.display = rtspRunning ? '' : 'none';
    if (chipHls)  chipHls.style.display  = rtspRunning ? '' : 'none';

    // Show/hide RTSP bitrate slider in stream settings
    const bitrateRow = document.getElementById('row-rtsp-bitrate');
    if (bitrateRow) bitrateRow.style.display = rtspRunning ? '' : 'none';

    if (s.rtsp && s.rtsp.urls) {
      const rtsp = s.rtsp.urls.rtsp || 'rtsp://…';
      const hls  = s.rtsp.urls.hls  || 'http://…/index.m3u8';
      setText('chip-rtsp-url', rtsp);
      setText('chip-hls-url',  hls);
    }
  } catch(e) { /* ignore */ }
}

// ── System info ────────────────────────────────────────────────────────────
async function loadSystemInfo() {
  try {
    const s = await api('GET', '/system');

    // CPU temp with color coding
    const tempEl = document.getElementById('sys-temp');
    if (s.cpu_temp_c != null) {
      tempEl.textContent = s.cpu_temp_c.toFixed(1) + ' °C';
      tempEl.className = 'metric-val' + (s.cpu_temp_c > 75 ? ' crit' : s.cpu_temp_c > 60 ? ' warn' : ' ok');
    }

    // CPU load
    const cpuEl = document.getElementById('sys-cpu');
    if (s.cpu_usage_pct != null) {
      cpuEl.textContent = s.cpu_usage_pct.toFixed(1) + ' %';
      cpuEl.className = 'metric-val' + (s.cpu_usage_pct > 85 ? ' crit' : s.cpu_usage_pct > 65 ? ' warn' : '');
    }

    if (s.memory) {
      setText('sys-ram-free',  s.memory.available_mb + ' MB');
      setText('sys-ram-total', s.memory.total_mb + ' MB');
    }

    if (s.disk) {
      setText('sys-disk-free',  s.disk.free_gb.toFixed(1) + ' GB');
      setText('sys-disk-total', s.disk.total_gb.toFixed(1) + ' GB');
    }

    setText('sys-host',   s.hostname  || '—');
    setText('sys-pi',     s.pi_model  || '—');
    setText('sys-uptime', s.uptime_seconds != null ? fmtUptime(s.uptime_seconds) : '—');
  } catch(e) { /* ignore */ }
}

// ── Network & services config ──────────────────────────────────────────────
function setVal(id, v) { const el = document.getElementById(id); if (el) el.value = v; }
function setChk(id, v) { const el = document.getElementById(id); if (el) el.checked = !!v; }

async function loadNetworkConfig() {
  try {
    const cfg = await api('GET', '/config');
    const m = (cfg.streaming || {}).mjpeg || {};
    const r = (cfg.streaming || {}).rtsp  || {};
    const w = cfg.web || {};
    const auth = w.auth || {};

    setVal('cfg-mjpeg-port',    m.port    ?? 8080);
    setVal('mjpeg-quality',     m.quality ?? 85);
    setVal('cfg-mjpeg-fps',     m.fps     ?? 15);

    setVal('cfg-rtsp-port',  r.port        ?? 8554);
    setVal('cfg-rtsp-name',  r.stream_name ?? 'cam');
    setChk('cfg-hls-enabled', r.enable_hls ?? true);
    setVal('cfg-hls-port',   r.hls_port    ?? 8888);

    setVal('cfg-web-port',   w.port ?? 8080);
    setChk('cfg-auth-enabled', auth.enabled ?? false);
    setVal('cfg-auth-user',  auth.username ?? 'admin');
    toggleAuthFields(auth.enabled ?? false);
  } catch(e) { /* ignore on load */ }
}

function toggleAuthFields(enabled) {
  const el = document.getElementById('cfg-auth-fields');
  if (el) el.style.display = enabled ? '' : 'none';
}

async function saveMjpegConfig() {
  btnLoading('btn-cfg-mjpeg', true, 'Speichern…');
  try {
    const port    = parseInt(document.getElementById('cfg-mjpeg-port').value);
    const quality = parseInt(document.getElementById('mjpeg-quality').value);
    const fps     = parseInt(document.getElementById('cfg-mjpeg-fps').value);
    await api('POST', '/config', { streaming: { mjpeg: { port, quality, fps } } });
    toast('MJPEG-Einstellungen gespeichert');
  } catch(e) { toast('Fehler: ' + e.message, 'error'); }
  finally { btnLoading('btn-cfg-mjpeg', false); }
}

async function saveRtspConfig() {
  btnLoading('btn-cfg-rtsp', true, 'Speichern…');
  try {
    const port        = parseInt(document.getElementById('cfg-rtsp-port').value);
    const stream_name = document.getElementById('cfg-rtsp-name').value.trim();
    const enable_hls  = document.getElementById('cfg-hls-enabled').checked;
    const hls_port    = parseInt(document.getElementById('cfg-hls-port').value);
    await api('POST', '/config', { streaming: { rtsp: { port, stream_name, enable_hls, hls_port } } });
    toast('RTSP-Einstellungen gespeichert');
  } catch(e) { toast('Fehler: ' + e.message, 'error'); }
  finally { btnLoading('btn-cfg-rtsp', false); }
}

async function saveWebConfig() {
  btnLoading('btn-cfg-web', true, 'Speichern…');
  try {
    const port    = parseInt(document.getElementById('cfg-web-port').value);
    const enabled = document.getElementById('cfg-auth-enabled').checked;
    const username = document.getElementById('cfg-auth-user').value.trim();
    const passRaw  = document.getElementById('cfg-auth-pass').value;
    const auth = { enabled, username };
    if (passRaw) auth.password = passRaw;  // only send if not blank
    await api('POST', '/config', { web: { port, auth } });
    toast('Web-Einstellungen gespeichert — Neustart erforderlich');
  } catch(e) { toast('Fehler: ' + e.message, 'error'); }
  finally { btnLoading('btn-cfg-web', false); }
}

// ── Camera restart ─────────────────────────────────────────────────────────
async function restartCamera() {
  btnLoading('btn-restart-cam', true, 'Neustart…');
  try {
    await api('POST', '/system/restart-camera');
    toast('Kamera wird neu gestartet…');
    setTimeout(loadCameraStatus, 3000);
  } catch(e) {
    toast('Fehler: ' + e.message, 'error');
  } finally {
    btnLoading('btn-restart-cam', false);
  }
}

// ── Snapshot ───────────────────────────────────────────────────────────────
async function takeSnapshot() {
  const save = document.getElementById('chk-snap-save').checked;
  btnLoading('btn-snapshot', true, 'Aufnehmen…');
  try {
    const url = '/snapshot' + (save ? '?save=true' : '');
    window.open(url, '_blank');
    if (save) {
      toast('Snapshot gespeichert');
      await loadSnapshotList();
    } else {
      toast('Snapshot geöffnet');
    }
  } catch(e) {
    toast('Fehler: ' + e.message, 'error');
  } finally {
    btnLoading('btn-snapshot', false);
  }
}

async function loadSnapshotList() {
  try {
    const items = await api('GET', '/snapshot/list');
    const list = document.getElementById('snapshot-list');
    if (!items || items.length === 0) {
      list.innerHTML = '<span style="font-size:0.72rem;color:var(--text-faint)">Keine gespeicherten Snapshots</span>';
      return;
    }
    const last5 = items.slice(-5).reverse();
    list.innerHTML = last5.map(item => `
      <div class="snapshot-item">
        <span class="snapshot-name">${item.filename || item.name || '—'}</span>
        <span class="snapshot-meta">${item.size_kb ? item.size_kb + ' KB' : ''}</span>
        <a class="snapshot-dl" href="/snapshots/${item.filename}" download title="Herunterladen">↓</a>
      </div>
    `).join('');
  } catch(e) {
    document.getElementById('snapshot-list').innerHTML = '';
  }
}

// ── Stream settings ────────────────────────────────────────────────────────
async function toggleRtsp(enabled) {
  const toggle = document.getElementById('chk-rtsp');
  const bitrateRow = document.getElementById('row-rtsp-bitrate');
  toggle._busy = true;
  try {
    await api('POST', enabled ? '/stream/rtsp/start' : '/stream/rtsp/stop');
    await api('POST', '/config', { streaming: { rtsp: { enabled } } });
    if (bitrateRow) bitrateRow.style.display = enabled ? '' : 'none';
    toast(enabled ? 'RTSP gestartet' : 'RTSP gestoppt');
    loadStreamStatus();
  } catch(e) {
    toggle.checked = !enabled;  // revert on error
    toast('Fehler: ' + e.message, 'error');
  } finally {
    toggle._busy = false;
  }
}

async function applyStreamSettings() {
  const resolution   = document.getElementById('sel-resolution').value;
  const framerate    = parseInt(document.getElementById('sel-framerate').value);
  const bitrate_kbps = parseInt(document.getElementById('sl-bitrate').value);

  btnLoading('btn-stream-apply', true, 'Wird angewendet…');
  try {
    const result = await api('POST', '/camera/settings', { resolution, framerate });
    await api('POST', '/stream/rtsp/settings', { resolution, framerate, bitrate_kbps });

    const img = document.getElementById('stream-img');
    img.src = '/stream?' + Date.now();

    toast('Streameinstellungen übernommen');
    if (result.restarted) toast('Kamera wurde neu gestartet');
  } catch(e) {
    toast('Fehler: ' + e.message, 'error');
  } finally {
    btnLoading('btn-stream-apply', false);
  }
}

// ── Image settings ─────────────────────────────────────────────────────────
async function applyImageSettings() {
  const brightness    = parseInt(document.getElementById('sl-brightness').value) / 100;
  const contrast      = parseInt(document.getElementById('sl-contrast').value) / 100;
  const saturation    = parseInt(document.getElementById('sl-saturation').value) / 100;
  const sharpness     = parseInt(document.getElementById('sl-sharpness').value) / 100;
  const exposure_mode    = document.getElementById('sel-exposure').value;
  const awb_mode         = document.getElementById('sel-awb').value;
  const noise_reduction  = document.getElementById('sel-noise-reduction').value;
  const vflip            = document.getElementById('chk-vflip').checked;
  const hflip            = document.getElementById('chk-hflip').checked;
  const autofocus        = document.getElementById('chk-autofocus').checked;
  const hdr              = document.getElementById('chk-hdr').checked;

  btnLoading('btn-img-apply', true, 'Wird angewendet…');
  try {
    const result = await api('POST', '/camera/settings', {
      brightness, contrast, saturation, sharpness,
      exposure_mode, awb_mode, noise_reduction, vflip, hflip, autofocus, hdr,
    });
    toast('Bildeinstellungen übernommen');
    if (result.restarted) toast('Kamera wurde neu gestartet');
  } catch(e) {
    toast('Fehler: ' + e.message, 'error');
  } finally {
    btnLoading('btn-img-apply', false);
  }
}

// ── Update ─────────────────────────────────────────────────────────────────
let _updatePollTimer = null;

async function checkUpdate() {
  btnLoading('btn-check-update', true, 'Suche…');
  try {
    const s = await api('POST', '/update/check');
    renderUpdateStatus(s);
  } catch(e) {
    setUpdateMsg('Fehler: ' + e.message, 'error');
  } finally {
    btnLoading('btn-check-update', false);
  }
}

async function doUpdate(version) {
  const msg = version
    ? `v${version} wird installiert. Danach startet BambuCam neu. Fortfahren?`
    : 'BambuCam wird aktualisiert und danach automatisch neu gestartet. Fortfahren?';
  if (!confirm(msg)) return;
  const body = version ? {version} : null;
  if (!version) btnLoading('btn-do-update', true, 'Update läuft…');
  try {
    await api('POST', '/update/start', body);
    startUpdatePolling();
  } catch(e) {
    toast('Fehler beim Update: ' + e.message, 'error');
    if (!version) btnLoading('btn-do-update', false);
  }
}

function startUpdatePolling() {
  if (_updatePollTimer) clearInterval(_updatePollTimer);
  _updatePollTimer = setInterval(async () => {
    try {
      const s = await api('GET', '/update/status');
      renderUpdateStatus(s);
      if (['success', 'error', 'idle', 'up_to_date'].includes(s.state)) {
        clearInterval(_updatePollTimer);
        _updatePollTimer = null;
        if (s.state === 'success') {
          let countdown = 5;
          const tick = () => {
            setUpdateMsg(`Update erfolgreich! Seite wird in ${countdown}s neu geladen…`, 'ok');
            if (countdown-- > 0) setTimeout(tick, 1000);
            else location.reload();
          };
          tick();
        }
      }
    } catch(e) { /* network lost during restart — expected */ }
  }, 1000);
}

function renderUpdateStatus(s) {
  setText('upd-current', s.current_version ? 'v' + s.current_version : '—');

  const latestEl = document.getElementById('upd-latest');
  if (s.update_available) {
    latestEl.textContent = 'v' + s.latest_version;
    latestEl.className = 'info-val accent';
  } else if (s.state === 'up_to_date') {
    latestEl.textContent = 'Aktuell';
    latestEl.className = 'info-val';
  } else {
    latestEl.textContent = s.latest_version ? 'v' + s.latest_version : '—';
    latestEl.className = 'info-val';
  }

  const releaseRow = document.getElementById('upd-release-row');
  if (s.latest_release && s.latest_release.html_url) {
    releaseRow.style.display = '';
    const lnk = document.getElementById('upd-release-link');
    lnk.href = s.latest_release.html_url;
    lnk.textContent = (s.latest_release.name || 'v' + s.latest_version) + ' →';
  } else {
    releaseRow.style.display = 'none';
  }

  const inProgress = ['downloading', 'installing', 'restarting'].includes(s.state);
  const pw = document.getElementById('upd-progress-wrap');
  pw.style.display = inProgress ? '' : 'none';
  document.getElementById('upd-progress-fill').style.width = (s.progress || 0) + '%';
  document.getElementById('upd-progress-label').textContent = s.message || '';

  if (s.state === 'error') {
    setUpdateMsg(s.error || 'Unbekannter Fehler', 'error');
  } else if (s.state === 'up_to_date') {
    setUpdateMsg('✓ BambuCam ist aktuell', 'ok');
  } else if (s.state === 'available') {
    setUpdateMsg('Update verfügbar: v' + s.latest_version, 'ok');
  } else if (!inProgress && s.message) {
    setUpdateMsg(s.message, 'dim');
  } else if (!inProgress) {
    setUpdateMsg('', 'dim');
  }

  const btnDo    = document.getElementById('btn-do-update');
  const btnCheck = document.getElementById('btn-check-update');

  if (s.update_available && !inProgress) {
    btnDo.style.display = '';
    btnDo.disabled = false;
    btnDo.innerHTML = `Auf v${s.latest_version} aktualisieren`;
    btnCheck.style.display = '';
    btnCheck.disabled = false;
    if (btnCheck.dataset.origLabel) btnCheck.innerHTML = btnCheck.dataset.origLabel;
  } else if (inProgress) {
    btnDo.style.display = '';
    btnDo.disabled = true;
    btnDo.innerHTML = `<span class="spinner"></span> Update läuft…`;
    btnCheck.style.display = 'none';
  } else if (s.state === 'error') {
    btnDo.style.display = 'none';
    btnCheck.style.display = '';
    btnCheck.disabled = false;
    btnCheck.innerHTML = 'Erneut versuchen';
    btnCheck.className = 'btn btn-danger-outline';
  } else {
    btnDo.style.display = 'none';
    btnCheck.style.display = '';
    btnCheck.disabled = false;
    if (btnCheck.dataset.origLabel) btnCheck.innerHTML = btnCheck.dataset.origLabel;
    btnCheck.className = 'btn btn-secondary';
  }
}

// ── Version history / downgrade ────────────────────────────────────────────
let _releasesLoaded = false;

function toggleReleases() {
  const panel = document.getElementById('releases-panel');
  const btn   = document.getElementById('btn-show-releases');
  const open  = panel.style.display === 'none';
  panel.style.display = open ? '' : 'none';
  btn.textContent = open ? 'Versionshistorie ▴' : 'Versionshistorie ▾';
  if (open && !_releasesLoaded) loadReleases();
}

async function loadReleases() {
  const list = document.getElementById('releases-list');
  list.innerHTML = '<div style="color:var(--text-dim);font-size:0.78rem;text-align:center;padding:0.5rem">Lade…</div>';
  try {
    const releases = await api('GET', '/update/releases');
    _releasesLoaded = true;
    renderReleases(releases);
  } catch(e) {
    list.innerHTML = `<div style="color:var(--danger);font-size:0.78rem;padding:0.4rem">Fehler: ${e.message}</div>`;
  }
}

function renderReleases(releases) {
  const list = document.getElementById('releases-list');
  if (!releases || releases.length === 0) {
    list.innerHTML = '<div style="color:var(--text-dim);font-size:0.78rem;text-align:center;padding:0.5rem">Keine Versionen gefunden</div>';
    return;
  }
  const currentEl = document.getElementById('upd-current');
  const currentVer = (currentEl ? currentEl.textContent : '').replace(/^v/, '');
  list.innerHTML = '';
  releases.forEach((r, i) => {
    const ver = r.tag_name ? r.tag_name.replace(/^v/, '') : '';
    const isCurrent = ver && ver === currentVer;
    const isLatest  = i === 0;
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;align-items:center;gap:0.5rem;padding:0.3rem 0.4rem;border-radius:6px;background:var(--card-bg2,rgba(255,255,255,0.04))';
    const badges = [];
    if (isCurrent) badges.push('<span style="font-size:0.68rem;padding:0.1rem 0.4rem;border-radius:4px;background:var(--accent);color:#fff">Aktuell</span>');
    if (isLatest && !isCurrent) badges.push('<span style="font-size:0.68rem;padding:0.1rem 0.4rem;border-radius:4px;background:var(--success,#22c55e);color:#fff">Neueste</span>');
    const relName = (r.name || '').replace(/^v[\d.]+\s*[-–]?\s*/,'').trim();
    const desc = relName || (r.body ? r.body.split('\n').find(l => l.trim()) || '' : '');
    const descHtml = desc ? `<span style="font-size:0.75rem;color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:200px" title="${desc.replace(/"/g,'&quot;')}">${desc}</span>` : '';
    const installBtn = isCurrent ? '' : `<button onclick="installVersion('${ver}')" style="margin-left:auto;flex-shrink:0;font-size:0.72rem;padding:0.15rem 0.5rem;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--text);cursor:pointer" onmouseover="this.style.background='var(--accent)';this.style.color='#fff'" onmouseout="this.style.background='transparent';this.style.color='var(--text)'">Installieren</button>`;
    row.innerHTML = `<span style="font-weight:600;font-size:0.82rem;flex-shrink:0">v${ver}</span>${badges.join('')}${descHtml}${installBtn}`;
    list.appendChild(row);
  });
}

async function installVersion(version) {
  await doUpdate(version);
}

async function loadInitialUpdateStatus() {
  try {
    const s = await api('GET', '/update/status');
    renderUpdateStatus(s);
    if (s.current_version) {
      document.getElementById('hdr-version').textContent = 'v' + s.current_version;
    }
    if (['downloading', 'installing', 'restarting'].includes(s.state)) {
      startUpdatePolling();
    }
  } catch(e) { /* ignore */ }
}

// ── DOM helpers ────────────────────────────────────────────────────────────
function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function setUpdateMsg(msg, type) {
  const el = document.getElementById('upd-msg');
  if (!el) return;
  el.textContent = msg;
  el.className = 'update-state-msg ' + (type || 'dim');
}

function toggleRow(id, show) {
  const el = document.getElementById(id);
  if (!el) return;
  if (show) el.classList.remove('hidden');
  else el.classList.add('hidden');
}

// ── Stream img — only attach MJPEG src when endpoint exists ───────────────
(function initStreamImg() {
  const img = document.getElementById('stream-img');
  img.addEventListener('load', () => {
    document.querySelector('.stream-view').classList.add('live');
  });
  fetch('/stream', { method: 'HEAD' }).then(r => {
    if (r.ok) img.src = '/stream';
  }).catch(() => {});
})();

// ── Settings modal ─────────────────────────────────────────────────────────
function openSettings() {
  document.getElementById('settings-overlay').classList.add('open');
  document.body.style.overflow = 'hidden';
}
function closeSettings() {
  document.getElementById('settings-overlay').classList.remove('open');
  document.body.style.overflow = '';
}
// close on backdrop click
document.getElementById('settings-overlay').addEventListener('click', function(e) {
  if (e.target === this) closeSettings();
});
// close on Escape
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') closeSettings();
});

// ── Boot ────────────────────────────────────────────────────────────────────
init();
