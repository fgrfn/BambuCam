'use strict';

// ── Module-level camera model state ───────────────────────────────────────
let resolutionMaxFps = {};  // populated on load from camera model data
let bitratePolicy = null;
let bitrateLimitsByResolution = {};

const streamEditorState = {
  dirty: false,
  applying: false,
  awaitingConfirmation: null,
  modelKey: null,
  requestSequence: 0,
  revision: 0,
};

// Polling may update live status, but must not overwrite unsubmitted edits.
function markStreamSettingsDirty() {
  streamEditorState.dirty = true;
  streamEditorState.revision += 1;
}

// Bounds are supplied by the backend for the actual encode stream.
function updateBitrateLimits() {
  const slider = document.getElementById('sl-bitrate');
  if (!slider) return;
  const resolution = document.getElementById('sel-resolution')?.value;
  const limits = bitrateLimitsByResolution[resolution] || bitratePolicy;
  slider.disabled = !limits;
  if (!limits) return;
  slider.min = limits.min_kbps;
  slider.max = limits.max_kbps;
  slider.value = Math.max(limits.min_kbps, Math.min(Number(slider.value), limits.max_kbps));
  setText('val-bitrate', slider.value);
  setText('hint-bitrate', tr('stream.bitrateLimit', {
    min: limits.min_kbps, max: limits.max_kbps, resolution: limits.resolution || resolution || '—',
  }));
}
document.getElementById('sel-resolution')?.addEventListener('change', updateBitrateLimits);

function tr(key, values) {
  return window.BambuCamI18n ? window.BambuCamI18n.t(key, values) : key;
}

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

function markRestartRequired(port) {
  const banner = document.getElementById('restart-required-banner');
  if (banner) banner.hidden = false;
  let target = window.location.href;
  if (port) {
    const url = new URL(window.location.href);
    url.port = String(port);
    url.pathname = '/';
    url.search = '';
    url.hash = '';
    target = url.toString();
  }
  localStorage.setItem('bambucamRestartRequired', target);
}

async function restartApplication() {
  btnLoading('btn-restart-app', true, tr('restart.loading'));
  const target = localStorage.getItem('bambucamRestartRequired') || window.location.href;
  try {
    await api('POST', '/system/restart');
    localStorage.removeItem('bambucamRestartRequired');
    toast(tr('restart.started'));
    setTimeout(() => { window.location.href = target; }, 4000);
  } catch (e) {
    toast(tr('restart.failed', { message: e.message }), 'error');
    btnLoading('btn-restart-app', false);
  }
}

async function rebootSystem() {
  if (!confirm(tr('systemReboot.confirm'))) return;
  btnLoading('btn-reboot-system', true, tr('systemReboot.scheduling'));
  try {
    await api('POST', '/system/reboot', { confirm: 'reboot' });
    closeSettings();
    toast(tr('systemReboot.started'));
  } catch (e) {
    toast(tr('systemReboot.failed', { message: e.message }), 'error');
    btnLoading('btn-reboot-system', false);
  }
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
    btn.innerHTML = `<span class="spinner"></span> ${label || tr('common.wait')}`;
  } else {
    if (btn.dataset.origLabel) btn.innerHTML = btn.dataset.origLabel;
    if (window.BambuCamI18n) window.BambuCamI18n.translateDocument(btn);
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
  if (localStorage.getItem('bambucamRestartRequired')) {
    const banner = document.getElementById('restart-required-banner');
    if (banner) banner.hidden = false;
  }
  await Promise.allSettled([
    loadCameraStatus(),
    loadStreamStatus(),
    loadSystemInfo(),
    loadNetworkConfig(),
    loadSnapshotList(),
  ]);
  loadInitialUpdateStatus();
  document.getElementById('sl-bitrate')?.addEventListener('input', markStreamSettingsDirty);

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
      txt.textContent = tr('header.cameraActive');
    } else {
      dot.className = 'status-dot error';
      txt.textContent = tr('header.cameraStopped');
    }

    // Conditional controls
    toggleRow('row-autofocus', s.has_autofocus);
    toggleRow('row-hdr',       s.has_hdr);
    toggleRow('row-zoom',      s.has_zoom);
    if (s.has_zoom) {
      const zoomSlider = document.getElementById('sl-zoom');
      zoomSlider.max = String(s.max_zoom || 8);
      zoomSlider.value = String(s.zoom || 1);
      setText('val-zoom', Number(s.zoom || 1).toFixed(1) + '×');
    }

    await loadModelCapabilities(s);
  } catch(e) {
    const dot = document.getElementById('status-dot');
    dot.className = 'status-dot error';
    document.getElementById('status-text').textContent = tr('header.connectionError');
  }
}

async function loadModelCapabilities(status) {
  if (!status.model) return;
  const requestSequence = ++streamEditorState.requestSequence;
  try {
    const models = await api('GET', '/camera/models');
    // Overlapping status polls can complete out of order.
    if (requestSequence !== streamEditorState.requestSequence) return;
    const model = models.find(m => m.name === status.model);
    if (!model) return;

    const resSel = document.getElementById('sel-resolution');
    const fpsSel = document.getElementById('sel-framerate');
    const modelKey = [status.backend, status.device, status.model].join('|');
    const cameraChanged = streamEditorState.modelKey !== modelKey;
    if (cameraChanged) {
      // A different physical camera cannot inherit the previous camera's draft.
      streamEditorState.modelKey = modelKey;
      streamEditorState.dirty = false;
      streamEditorState.awaitingConfirmation = null;
    }

    const pending = streamEditorState.awaitingConfirmation;
    if (pending && pending.resolution === status.resolution &&
        pending.framerate === Number(status.framerate)) {
      streamEditorState.awaitingConfirmation = null;
      streamEditorState.dirty = false;
    }

    // Stream controls show video-safe modes, not photo-only native sensor modes.
    const outputs = status.video_output_modes ?? status.output_modes ?? [];
    const available = [...new Set(
      outputs.length ? outputs.map(m => m.resolution) : model.supported_resolutions
    )];
    if (!available.length) return;
    resolutionMaxFps = status.video_resolution_max_framerates ||
      status.resolution_max_framerates || model.resolution_max_framerates || {};

    const preserveDraft = !cameraChanged && (
      streamEditorState.dirty || streamEditorState.applying ||
      !!streamEditorState.awaitingConfirmation
    );
    const preferredResolution = preserveDraft ? resSel.value : status.resolution;
    // If a saved camera mode is photo-only, prefer the largest safe video size.
    const videoDefault = available.includes(status.resolution) ? status.resolution :
      available.reduce((best, size) => {
        const pixels = value => value.split('x').map(Number).reduce((a, b) => a * b, 1);
        return pixels(size) > pixels(best) ? size : best;
      });
    const selectedResolution = available.includes(preferredResolution)
      ? preferredResolution : videoDefault;
    if (preserveDraft && selectedResolution !== preferredResolution) {
      // A previously selected mode has disappeared from the new capability list.
      streamEditorState.dirty = false;
      streamEditorState.awaitingConfirmation = null;
    }

    const pixelCount = value => value.split('x').map(Number).reduce((a, b) => a * b, 1);
    const options = [...available].sort((a, b) => pixelCount(b) - pixelCount(a)).map(r => {
      const output = outputs.find(m => m.resolution === r);
      // Older status payloads only have sensor rates; preserve their labels.
      const maximum = status.video_output_modes != null
        ? (resolutionMaxFps[r] ?? output?.max_fps ?? output?.sensor_max_fps)
        : (output?.sensor_max_fps ?? resolutionMaxFps[r]);
      const parts = [];
      if (output?.aspect) parts.push(output.aspect);
      // Field of view matters more than pixels for a printer camera: some native
      // modes (OV5647 1920x1080) only read out a centre window of the sensor.
      const fov = output?.fov_width_pct;
      if (fov != null) {
        parts.push(fov >= 97 ? tr('camera.mode.fullWidth') : tr('camera.mode.cropped', { value: fov }));
      }
      if (output?.kind === 'scaled') {
        parts.push(tr('camera.mode.scaledFrom', { value: output.sensor_resolution }));
      } else if (output?.kind !== 'native') {
        parts.push(tr('camera.mode.model'));
      }
      parts.push(tr('camera.maxFps', { value: maximum }));
      if (output?.source === 'model_fallback') parts.push(tr('camera.mode.fallback'));
      return { value: r, label: r.replace('x', '×') + ' (' + parts.join(', ') + ')' };
    });

    // Do not replace focused select elements on every polling tick.
    const modesChanged = resSel.options.length !== options.length ||
      options.some((option, index) => {
        const existing = resSel.options[index];
        return String(existing.value) !== option.value ||
          (existing.text || existing.label) !== option.label;
      });
    if (modesChanged) {
      resSel.replaceChildren(...options.map(o =>
        new Option(o.label, o.value, false, o.value === selectedResolution)
      ));
    }
    if (resSel.value !== selectedResolution) resSel.value = selectedResolution;

    function buildFpsOptions(res, preferredFps) {
      const maximum = Math.max(1, Math.floor(Number(
        resolutionMaxFps[res] ?? model.max_framerate
      ) || 30));
      const values = [...new Set([...model.supported_framerates, maximum, preferredFps]
        .map(Number))]
        .filter(f => Number.isInteger(f) && f >= 1 && f <= maximum)
        .sort((a, b) => b - a);
      const chosen = values.includes(preferredFps)
        ? preferredFps : (values.find(f => f <= preferredFps) ?? values[values.length - 1]);
      const changed = fpsSel.options.length !== values.length ||
        values.some((fps, index) => String(fpsSel.options[index].value) !== String(fps));
      if (changed) {
        fpsSel.replaceChildren(...values.map(f =>
          new Option(f + ' fps', f, false, f === chosen)
        ));
      }
      if (fpsSel.value !== String(chosen)) fpsSel.value = String(chosen);
    }

    const preferredFps = preserveDraft ? Number(fpsSel.value) :
      Number(status.framerate || fpsSel.value || 15);
    buildFpsOptions(selectedResolution, preferredFps);

    resSel.onchange = () => {
      markStreamSettingsDirty();
      buildFpsOptions(resSel.value, Number(fpsSel.value));
      if (typeof updateBitrateLimits === 'function') updateBitrateLimits();
    };
    fpsSel.onchange = markStreamSettingsDirty;
  } catch(e) { /* keep the current draft if camera capabilities are unavailable */ }
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
      if (fpsEl) fpsEl.textContent = actualFps != null
        ? ` (${tr('camera.actualFps', { value: actualFps })})`
        : '';

      const cnt = s.mjpeg.clients || 0;
      const vb = document.getElementById('viewers-badge');
      if (cnt > 0) {
        vb.classList.remove('hidden');
        setText('viewers-count', cnt);
        setText('viewers-label', tr(cnt === 1 ? 'stream.viewerOne' : 'stream.viewerMany'));
      } else {
        vb.classList.add('hidden');
      }
    }

    if (s.rtsp?.bitrate_limits) {
      bitratePolicy = s.rtsp.bitrate_limits;
      bitrateLimitsByResolution = s.rtsp.bitrate_limits_by_resolution || {};
      updateBitrateLimits();
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

    renderThrottling(s.throttling);

    setText('sys-host',   s.hostname  || '—');
    setText('sys-pi',     s.pi_model  || '—');
    setText('sys-uptime', s.uptime_seconds != null ? fmtUptime(s.uptime_seconds) : '—');
    toggleRow('system-reboot-section', !!s.can_reboot);
  } catch(e) { /* ignore */ }
}

function renderThrottling(state) {
  toggleRow('sys-throttle-row', state != null);
  const el = document.getElementById('sys-throttle');
  if (!state || !el) return;
  const labels = {
    under_voltage: tr('system.throttle.under_voltage'),
    freq_capped: tr('system.throttle.freq_capped'),
    throttled: tr('system.throttle.throttled'),
    soft_temp_limit: tr('system.throttle.soft_temp_limit'),
  };
  const names = flags => flags.map(flag => labels[flag] || flag).join(', ');
  if (state.active.length) {
    el.textContent = tr('system.throttle.now', { value: names(state.active) });
    el.className = 'info-val crit';
  } else if (state.since_boot.length) {
    el.textContent = tr('system.throttle.sinceBoot', { value: names(state.since_boot) });
    el.className = 'info-val warn';
  } else {
    el.textContent = tr('system.throttle.ok');
    el.className = 'info-val';
  }
  el.title = state.raw;
}

// ── Network & services config ──────────────────────────────────────────────
function setVal(id, v) { const el = document.getElementById(id); if (el) el.value = v; }
function setChk(id, v) { const el = document.getElementById(id); if (el) el.checked = !!v; }

async function loadNetworkConfig() {
  try {
    const cfg = await api('GET', '/config');
    const m = (cfg.streaming || {}).mjpeg || {};
    const r = (cfg.streaming || {}).rtsp  || {};
    const c = cfg.camera || {};
    const w = cfg.web || {};
    const auth = w.auth || {};

    setVal('cfg-mjpeg-port',    m.port    ?? 8080);
    setVal('mjpeg-quality',     m.quality ?? 85);
    setVal('cfg-mjpeg-fps',     m.fps     ?? 15);

    setVal('cfg-rtsp-port',  r.port        ?? 8554);
    setVal('cfg-rtsp-name',  r.stream_name ?? 'cam');
    setChk('cfg-hls-enabled', r.enable_hls ?? true);
    setVal('cfg-hls-port',   r.hls_port    ?? 8888);
    if (!streamEditorState.dirty && !streamEditorState.applying &&
        !streamEditorState.awaitingConfirmation) {
      setVal('sl-bitrate', r.bitrate_kbps ?? 2000);
      setText('val-bitrate', r.bitrate_kbps ?? 2000);
    }
    updateBitrateLimits();

    setVal('sl-brightness', Math.round((c.brightness ?? 0) * 100));
    setText('val-brightness', Number(c.brightness ?? 0).toFixed(2));
    setVal('sl-contrast', Math.round((c.contrast ?? 1) * 100));
    setText('val-contrast', Number(c.contrast ?? 1).toFixed(1));
    setVal('sl-saturation', Math.round((c.saturation ?? 1) * 100));
    setText('val-saturation', Number(c.saturation ?? 1).toFixed(1));
    setVal('sl-sharpness', Math.round((c.sharpness ?? 1) * 100));
    setText('val-sharpness', Number(c.sharpness ?? 1).toFixed(1));
    setVal('sl-zoom', c.zoom ?? 1);
    setText('val-zoom', Number(c.zoom ?? 1).toFixed(1) + '×');
    setVal('sel-exposure', c.exposure_mode ?? 'auto');
    setVal('sel-awb', c.awb_mode ?? 'auto');
    setVal('sel-noise-reduction', c.noise_reduction ?? 'fast');
    setChk('chk-vflip', c.vflip ?? false);
    setChk('chk-hflip', c.hflip ?? false);
    setChk('chk-autofocus', c.autofocus ?? true);
    setChk('chk-hdr', c.hdr ?? false);

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
  btnLoading('btn-cfg-mjpeg', true, tr('settings.saving'));
  try {
    const port    = parseInt(document.getElementById('cfg-mjpeg-port').value);
    const quality = parseInt(document.getElementById('mjpeg-quality').value);
    const fps     = parseInt(document.getElementById('cfg-mjpeg-fps').value);
    const result = await api('POST', '/config', { streaming: { mjpeg: { port, quality, fps } } });
    if (result.restart_required) markRestartRequired(port);
    toast(tr('settings.mjpegSaved'));
  } catch(e) { toast(tr('common.error', { message: e.message }), 'error'); }
  finally { btnLoading('btn-cfg-mjpeg', false); }
}

async function saveRtspConfig() {
  btnLoading('btn-cfg-rtsp', true, tr('settings.saving'));
  try {
    const port        = parseInt(document.getElementById('cfg-rtsp-port').value);
    const stream_name = document.getElementById('cfg-rtsp-name').value.trim();
    const enable_hls  = document.getElementById('cfg-hls-enabled').checked;
    const hls_port    = parseInt(document.getElementById('cfg-hls-port').value);
    await api('POST', '/config', { streaming: { rtsp: { port, stream_name, enable_hls, hls_port } } });
    toast(tr('settings.rtspSaved'));
  } catch(e) { toast(tr('common.error', { message: e.message }), 'error'); }
  finally { btnLoading('btn-cfg-rtsp', false); }
}

async function saveWebConfig() {
  btnLoading('btn-cfg-web', true, tr('settings.saving'));
  try {
    const port    = parseInt(document.getElementById('cfg-web-port').value);
    const enabled = document.getElementById('cfg-auth-enabled').checked;
    const username = document.getElementById('cfg-auth-user').value.trim();
    const passRaw  = document.getElementById('cfg-auth-pass').value;
    const auth = { enabled, username };
    if (passRaw) auth.password = passRaw;  // only send if not blank
    const result = await api('POST', '/config', { web: { port, auth } });
    if (result.restart_required) markRestartRequired(port);
    toast(tr('settings.webSaved'));
  } catch(e) { toast(tr('common.error', { message: e.message }), 'error'); }
  finally { btnLoading('btn-cfg-web', false); }
}

// ── Camera restart ─────────────────────────────────────────────────────────
async function restartCamera() {
  btnLoading('btn-restart-cam', true, tr('restart.loading'));
  try {
    await api('POST', '/system/restart-camera');
    toast(tr('restart.started'));
    setTimeout(loadCameraStatus, 3000);
  } catch(e) {
    toast(tr('common.error', { message: e.message }), 'error');
  } finally {
    btnLoading('btn-restart-cam', false);
  }
}

// ── Snapshot ───────────────────────────────────────────────────────────────
async function takeSnapshot() {
  const save = document.getElementById('chk-snap-save').checked;
  btnLoading('btn-snapshot', true, tr('snapshot.taking'));
  try {
    const url = '/snapshot' + (save ? '?save=true' : '');
    window.open(url, '_blank');
    if (save) {
      toast(tr('snapshot.saved'));
      await loadSnapshotList();
    } else {
      toast(tr('snapshot.opened'));
    }
  } catch(e) {
    toast(tr('common.error', { message: e.message }), 'error');
  } finally {
    btnLoading('btn-snapshot', false);
  }
}

async function loadSnapshotList() {
  try {
    const items = await api('GET', '/snapshot/list');
    const list = document.getElementById('snapshot-list');
    if (!items || items.length === 0) {
      list.innerHTML = `<span style="font-size:0.72rem;color:var(--text-faint)">${tr('snapshot.none')}</span>`;
      return;
    }
    const last5 = items.slice(-5).reverse();
    list.innerHTML = last5.map(item => `
      <div class="snapshot-item">
        <span class="snapshot-name">${item.filename || item.name || '—'}</span>
        <span class="snapshot-meta">${item.size_kb ? item.size_kb + ' KB' : ''}</span>
        <a class="snapshot-dl" href="/snapshots/${item.filename}" download title="${tr('snapshot.download')}">↓</a>
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
    if (bitrateRow) bitrateRow.style.display = enabled ? '' : 'none';
    toast(tr(enabled ? 'stream.rtspStarted' : 'stream.rtspStopped'));
    loadStreamStatus();
  } catch(e) {
    toggle.checked = !enabled;  // revert on error
    toast(tr('common.error', { message: e.message }), 'error');
  } finally {
    toggle._busy = false;
  }
}

async function applyStreamSettings() {
  const resolution   = document.getElementById('sel-resolution').value;
  const framerate    = parseInt(document.getElementById('sel-framerate').value);
  const slider = document.getElementById('sl-bitrate');
  if (slider.disabled) {
    toast(tr('stream.bitrateUnavailable'), 'error');
    return;
  }
  const bitrate_kbps = Number(slider.value);
  if (!Number.isInteger(bitrate_kbps) || bitrate_kbps < Number(slider.min) ||
      bitrate_kbps > Number(slider.max)) {
    toast(tr('stream.bitrateInvalid', { min: slider.min, max: slider.max }), 'error');
    return;
  }

  if (streamEditorState.applying) return;
  const revision = streamEditorState.revision;
  const modelKey = streamEditorState.modelKey;
  streamEditorState.applying = true;
  btnLoading('btn-stream-apply', true, tr('stream.applying'));
  try {
    const result = await api('POST', '/stream/settings', {
      resolution, framerate, bitrate_kbps,
    });
    if (revision === streamEditorState.revision && modelKey === streamEditorState.modelKey) {
      // Keep the submitted values until the camera status confirms them.
      streamEditorState.awaitingConfirmation = { resolution, framerate };
      streamEditorState.dirty = true;
    }

    const img = document.getElementById('stream-img');
    img.src = '/stream?' + Date.now();

    toast(tr('stream.applied'));
    if (result.restarted) toast(tr('stream.cameraRestarted'));
    window.dispatchEvent(new CustomEvent('bambucam:configurationchange'));
  } catch(e) {
    toast(tr('common.error', { message: e.message }), 'error');
  } finally {
    streamEditorState.applying = false;
    btnLoading('btn-stream-apply', false);
  }
}

// ── Image settings ─────────────────────────────────────────────────────────
async function applyImageSettings() {
  const brightness    = parseInt(document.getElementById('sl-brightness').value) / 100;
  const contrast      = parseInt(document.getElementById('sl-contrast').value) / 100;
  const saturation    = parseInt(document.getElementById('sl-saturation').value) / 100;
  const sharpness     = parseInt(document.getElementById('sl-sharpness').value) / 100;
  const zoom          = Number(document.getElementById('sl-zoom').value);
  const exposure_mode    = document.getElementById('sel-exposure').value;
  const awb_mode         = document.getElementById('sel-awb').value;
  const noise_reduction  = document.getElementById('sel-noise-reduction').value;
  const vflip            = document.getElementById('chk-vflip').checked;
  const hflip            = document.getElementById('chk-hflip').checked;
  const autofocus        = document.getElementById('chk-autofocus').checked;
  const hdr              = document.getElementById('chk-hdr').checked;

  btnLoading('btn-img-apply', true, tr('stream.applying'));
  try {
    const imageSettings = {
      brightness, contrast, saturation, sharpness,
      exposure_mode, awb_mode, noise_reduction, vflip, hflip, autofocus, hdr,
    };
    if (!document.getElementById('row-zoom').classList.contains('hidden')) {
      imageSettings.zoom = zoom;
    }
    const result = await api('POST', '/camera/settings', imageSettings);
    toast(tr('image.applied'));
    if (result.restarted) toast(tr('stream.cameraRestarted'));
    window.dispatchEvent(new CustomEvent('bambucam:configurationchange'));
  } catch(e) {
    toast(tr('common.error', { message: e.message }), 'error');
  } finally {
    btnLoading('btn-img-apply', false);
  }
}

// ── Update ─────────────────────────────────────────────────────────────────
let _updatePollTimer = null;
let _lastUpdateStatus = null;
let _lastReleases = null;

function translatedUpdateProgress(state, fallback = '') {
  const keys = {
    checking: 'update.checking',
    preparing: 'update.preparing',
    downloading: 'update.downloading',
    installing: 'update.installing',
    restarting: 'update.restarting',
  };
  return keys[state] ? tr(keys[state]) : fallback;
}

async function checkUpdate() {
  btnLoading('btn-check-update', true, tr('update.checking'));
  try {
    const s = await api('POST', '/update/check');
    renderUpdateStatus(s);
  } catch(e) {
    setUpdateMsg(tr('common.error', { message: e.message }), 'error');
  } finally {
    btnLoading('btn-check-update', false);
  }
}

async function doUpdate(version) {
  const msg = version
    ? tr('update.confirmVersion', { version })
    : tr('update.confirm');
  if (!confirm(msg)) return;
  const body = version ? {version} : null;
  if (!version) btnLoading('btn-do-update', true, tr('update.running'));
  try {
    await api('POST', '/update/start', body);
    startUpdatePolling();
  } catch(e) {
    toast(tr('update.failed', { message: e.message }), 'error');
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
            setUpdateMsg(tr('update.successReload', { seconds: countdown }), 'ok');
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
  _lastUpdateStatus = s;
  setText('upd-current', s.current_version ? 'v' + s.current_version : '—');

  const latestEl = document.getElementById('upd-latest');
  if (s.update_available) {
    latestEl.textContent = 'v' + s.latest_version;
    latestEl.className = 'info-val accent';
  } else if (s.state === 'up_to_date') {
    latestEl.textContent = tr('update.upToDate');
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
  document.getElementById('upd-progress-label').textContent = translatedUpdateProgress(s.state, s.message || '');

  if (s.state === 'error') {
    setUpdateMsg(s.error || tr('update.unknownError'), 'error');
  } else if (s.state === 'up_to_date') {
    setUpdateMsg(tr('update.upToDateMessage'), 'ok');
  } else if (s.state === 'available') {
    setUpdateMsg(tr('update.availableVersion', { version: s.latest_version }), 'ok');
  } else if (!inProgress && s.message) {
    setUpdateMsg(translatedUpdateProgress(s.state, s.message), 'dim');
  } else if (!inProgress) {
    setUpdateMsg('', 'dim');
  }

  const btnDo    = document.getElementById('btn-do-update');
  const btnCheck = document.getElementById('btn-check-update');

  if (s.update_available && !inProgress) {
    btnDo.style.display = '';
    btnDo.disabled = false;
    btnDo.textContent = tr('update.toVersion', { version: s.latest_version });
    btnCheck.style.display = '';
    btnCheck.disabled = false;
    btnCheck.textContent = tr('update.check');
  } else if (inProgress) {
    btnDo.style.display = '';
    btnDo.disabled = true;
    btnDo.innerHTML = `<span class="spinner"></span> ${tr('update.running')}`;
    btnCheck.style.display = 'none';
  } else if (s.state === 'error') {
    btnDo.style.display = 'none';
    btnCheck.style.display = '';
    btnCheck.disabled = false;
    btnCheck.textContent = tr('update.retry');
    btnCheck.className = 'btn btn-danger-outline';
  } else {
    btnDo.style.display = 'none';
    btnCheck.style.display = '';
    btnCheck.disabled = false;
    btnCheck.textContent = tr('update.check');
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
  btn.textContent = tr(open ? 'update.historyOpen' : 'update.historyClosed');
  if (open && !_releasesLoaded) loadReleases();
}

async function loadReleases() {
  const list = document.getElementById('releases-list');
  list.innerHTML = `<div style="color:var(--text-dim);font-size:0.78rem;text-align:center;padding:0.5rem">${tr('common.loading')}</div>`;
  try {
    const releases = await api('GET', '/update/releases');
    _releasesLoaded = true;
    renderReleases(releases);
  } catch(e) {
    list.innerHTML = `<div style="color:var(--danger);font-size:0.78rem;padding:0.4rem">${tr('common.error', { message: e.message })}</div>`;
  }
}

function renderReleases(releases) {
  _lastReleases = releases;
  const list = document.getElementById('releases-list');
  if (!releases || releases.length === 0) {
    list.innerHTML = `<div style="color:var(--text-dim);font-size:0.78rem;text-align:center;padding:0.5rem">${tr('update.noReleases')}</div>`;
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
    if (isCurrent) badges.push(`<span style="font-size:0.68rem;padding:0.1rem 0.4rem;border-radius:4px;background:var(--accent);color:#fff">${tr('common.current')}</span>`);
    if (isLatest && !isCurrent) badges.push(`<span style="font-size:0.68rem;padding:0.1rem 0.4rem;border-radius:4px;background:var(--success,#22c55e);color:#fff">${tr('common.latest')}</span>`);
    const relName = (r.name || '').replace(/^v[\d.]+\s*[-–]?\s*/,'').trim();
    const desc = relName || (r.body ? r.body.split('\n').find(l => l.trim()) || '' : '');
    const descHtml = desc ? `<span style="font-size:0.75rem;color:var(--text-dim);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:200px" title="${desc.replace(/"/g,'&quot;')}">${desc}</span>` : '';
    const installBtn = isCurrent ? '' : `<button onclick="installVersion('${ver}')" style="margin-left:auto;flex-shrink:0;font-size:0.72rem;padding:0.15rem 0.5rem;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--text);cursor:pointer" onmouseover="this.style.background='var(--accent)';this.style.color='#fff'" onmouseout="this.style.background='transparent';this.style.color='var(--text)'">${tr('common.install')}</button>`;
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

window.addEventListener('bambucam:languagechange', () => {
  loadCameraStatus();
  loadStreamStatus();
  loadSnapshotList();
  if (_lastUpdateStatus) renderUpdateStatus(_lastUpdateStatus);
  if (_lastReleases) renderReleases(_lastReleases);
  const releasesPanel = document.getElementById('releases-panel');
  const releasesButton = document.getElementById('btn-show-releases');
  if (releasesPanel && releasesButton) {
    releasesButton.textContent = tr(
      releasesPanel.style.display === 'none' ? 'update.historyClosed' : 'update.historyOpen'
    );
  }
});

window.addEventListener('bambucam:configurationchange', () => {
  Promise.allSettled([
    loadCameraStatus(),
    loadStreamStatus(),
    loadNetworkConfig(),
  ]);
});

// ── Boot ────────────────────────────────────────────────────────────────────
init();
