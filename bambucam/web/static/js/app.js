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

    renderCameraSetupHint(s.setup_hint);
    consoleState.camera = s;
    renderConsole();

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
    consoleState.camera = null;
    renderConsole();
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
      if (output?.limited_by === 'h264_encoder') parts.push(tr('camera.mode.encoderLimit'));
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
    // The visible resolution cards mirror the (hidden) select, which stays the source of truth.
    if (typeof renderResolutionCards === 'function') renderResolutionCards(status, outputs, available);

    resSel.onchange = () => {
      markStreamSettingsDirty();
      buildFpsOptions(resSel.value, Number(fpsSel.value));
      if (typeof updateBitrateLimits === 'function') updateBitrateLimits();
      if (typeof renderResolutionCards === 'function') renderResolutionCards(status, outputs, available);
    };
    fpsSel.onchange = () => {
      markStreamSettingsDirty();
      if (typeof updateStreamSummary === 'function') updateStreamSummary();
    };
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
    consoleState.stream = s;
    renderConsole();
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
      tempEl.className = 'kv-val num' + (s.cpu_temp_c >= 80 ? ' crit' : s.cpu_temp_c >= 70 ? ' warn' : '');
    }

    // CPU load
    const cpuEl = document.getElementById('sys-cpu');
    if (s.cpu_usage_pct != null) {
      cpuEl.textContent = s.cpu_usage_pct.toFixed(1) + ' %';
      cpuEl.className = 'kv-val num' + (s.cpu_usage_pct > 85 ? ' crit' : s.cpu_usage_pct > 65 ? ' warn' : '');
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
    consoleState.system = s;
    renderConsole();
  } catch(e) { /* ignore */ }
}

function renderCameraSetupHint(hint) {
  const el = document.getElementById('cam-setup-hint');
  if (!el) return;
  toggleRow('cam-setup-hint', !!hint);
  if (!hint) return;
  const sensors = Object.entries(hint.sensors).map(([name, label]) => name + ' = ' + label).join(', ');
  const code = text => { const node = document.createElement('code'); node.textContent = text; return node; };
  el.replaceChildren(
    document.createTextNode(tr('camera.setupHint.intro', { value: hint.config_file })),
    document.createElement('br'),
    ...hint.lines.flatMap(line => [code(line), document.createElement('br')]),
    document.createTextNode(tr('camera.setupHint.cam0') + ' '),
    code(hint.cam0_line),
    document.createElement('br'),
    document.createTextNode(tr('camera.setupHint.sensors', { value: sensors })),
  );
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
    el.className = 'kv-val crit';
  } else if (state.since_boot.length) {
    el.textContent = tr('system.throttle.sinceBoot', { value: names(state.since_boot) });
    el.className = 'kv-val warn';
  } else {
    el.textContent = tr('system.throttle.ok');
    el.className = 'kv-val';
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
    const storage = cfg.streaming || {};
    setVal('cfg-snapshot-dir',  (storage.snapshot || {}).save_dir  ?? '/var/lib/bambucam/snapshots');
    setVal('cfg-timelapse-dir', (storage.timelapse || {}).save_dir ?? '/var/lib/bambucam/timelapse');
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

async function saveStorageConfig() {
  btnLoading('btn-cfg-storage', true, tr('settings.saving'));
  try {
    const snapshotDir  = document.getElementById('cfg-snapshot-dir').value.trim();
    const timelapseDir = document.getElementById('cfg-timelapse-dir').value.trim();
    const result = await api('POST', '/config', { streaming: {
      snapshot: { save_dir: snapshotDir },
      timelapse: { save_dir: timelapseDir },
    } });
    if (result.restart_required) markRestartRequired();
    toast(tr('settings.storageSaved'));
  } catch(e) { toast(tr('common.error', { message: e.message }), 'error'); }
  finally { btnLoading('btn-cfg-storage', false); }
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
// Snapshots are always saved, so they show up in the film strip and timeline.
async function takeSnapshot() {
  btnLoading('btn-snapshot', true, tr('snapshot.taking'));
  try {
    const response = await fetch('/snapshot?save=true', { credentials: 'same-origin' });
    if (!response.ok) {
      const err = await response.json().catch(() => ({ error: response.statusText }));
      throw new Error(err.error || response.statusText);
    }
    const before = new Set(snapshotItems.map(item => item.filename));
    await loadSnapshotList();
    const added = snapshotItems.find(item => !before.has(item.filename));
    newestSnapshot = added ? added.filename : null;
    renderSnapshots();
    toast(tr('snapshot.saved'));
  } catch(e) {
    toast(tr('common.error', { message: e.message }), 'error');
  } finally {
    btnLoading('btn-snapshot', false);
  }
}

async function loadSnapshotList() {
  try {
    snapshotItems = await api('GET', '/snapshot/list') || [];
  } catch(e) {
    snapshotItems = [];
  }
  renderSnapshots();
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
    if (typeof updateStreamSummary === 'function') updateStreamSummary();

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
  consoleState.update = s;
  setText('upd-current', s.current_version ? 'v' + s.current_version : '—');

  const latestEl = document.getElementById('upd-latest');
  if (s.update_available) {
    latestEl.textContent = 'v' + s.latest_version;
    latestEl.className = 'kv-val accent';
  } else if (s.state === 'up_to_date') {
    latestEl.textContent = tr('update.upToDate');
    latestEl.className = 'kv-val';
  } else {
    latestEl.textContent = s.latest_version ? 'v' + s.latest_version : '—';
    latestEl.className = 'kv-val';
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
    btnCheck.className = 'btn danger';
  } else {
    btnDo.style.display = 'none';
    btnCheck.style.display = '';
    btnCheck.disabled = false;
    btnCheck.textContent = tr('update.check');
    btnCheck.className = 'btn';
  }
  renderConsole();
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
  list.replaceChildren(el('div', 'muted small', tr('common.loading')));
  try {
    const releases = await api('GET', '/update/releases');
    _releasesLoaded = true;
    renderReleases(releases);
  } catch(e) {
    list.replaceChildren(el('div', 'small update-state-msg error', tr('common.error', { message: e.message })));
  }
}

function renderReleases(releases) {
  _lastReleases = releases;
  const list = document.getElementById('releases-list');
  if (!releases || releases.length === 0) {
    list.replaceChildren(el('div', 'muted small', tr('update.noReleases')));
    return;
  }
  const currentEl = document.getElementById('upd-current');
  const currentVer = (currentEl ? currentEl.textContent : '').replace(/^v/, '');
  list.replaceChildren(...releases.map((r, i) => {
    const ver = r.tag_name ? r.tag_name.replace(/^v/, '') : '';
    const isCurrent = ver && ver === currentVer;
    const row = el('div', 'release-row');
    row.append(el('span', 'ver', 'v' + ver));
    if (isCurrent) row.append(el('span', 'chip ok', tr('common.current')));
    if (i === 0 && !isCurrent) row.append(el('span', 'chip neutral', tr('common.latest')));
    const relName = (r.name || '').replace(/^v[\d.]+\s*[-–]?\s*/, '').trim();
    const desc = relName || (r.body ? r.body.split('\n').find(l => l.trim()) || '' : '');
    if (desc) {
      const descEl = el('span', 'desc', desc);
      descEl.title = desc;
      row.append(descEl);
    }
    if (!isCurrent) {
      const install = el('button', 'btn small', tr('common.install'));
      install.type = 'button';
      install.addEventListener('click', () => installVersion(ver));
      row.append(install);
    }
    return row;
  }));
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

// ── Settings drawer ────────────────────────────────────────────────────────
function openSettings() {
  document.getElementById('settings-overlay').classList.add('open');
  document.body.style.overflow = 'hidden';
  document.querySelector('#settings-overlay .drawer-head .icon-btn')?.focus();
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
  renderConsole();
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

// ── Console layout: tabs, health, checklist, recordings ───────────────────
const consoleState = { camera: null, stream: null, system: null, update: null };
let snapshotItems = [];
let newestSnapshot = null;
let timelapseSessions = [];
let timelapseStatus = null;
let timelineFilter = 'all';
let timelineLimit = 40;
let consoleReady = false;
const CONSOLE_VIEWS = ['live', 'recordings', 'system'];
const THEME_KEY = 'bambucam.theme';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  return node;
}

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', '#i-' + name);
  svg.append(use);
  return svg;
}

function showView(view) {
  const target = CONSOLE_VIEWS.includes(view) ? view : 'live';
  CONSOLE_VIEWS.forEach(name => {
    const section = document.getElementById('view-' + name);
    if (section) section.hidden = name !== target;
  });
  document.querySelectorAll('.tabs button[data-view]').forEach(button => {
    if (button.dataset.view === target) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  if (location.hash.slice(1) !== target) history.replaceState(null, '', '#' + target);
  // Snapshots are not polled; refresh them when the recordings are opened.
  if (target === 'recordings' && consoleReady) loadSnapshotList();
}

function applyTheme(mode) {
  if (mode === 'light' || mode === 'dark') document.documentElement.dataset.theme = mode;
  else delete document.documentElement.dataset.theme;
  document.querySelectorAll('[data-theme-set]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.themeSet === (mode || 'auto')));
  });
}

function setTheme(mode) {
  try {
    if (mode === 'auto') localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, mode);
  } catch (_) { /* storage unavailable: theme lasts for this page only */ }
  applyTheme(mode);
}

function toggleFullscreen() {
  const view = document.getElementById('stream-view');
  if (document.fullscreenElement) document.exitFullscreen();
  else if (view.requestFullscreen) view.requestFullscreen().catch(() => {});
}

function pixelsOf(resolution) {
  return String(resolution).split('x').map(Number).reduce((a, b) => a * b, 1);
}

function updateStreamSummary() {
  const res = document.getElementById('sel-resolution')?.value;
  const fps = document.getElementById('sel-framerate')?.value;
  if (res) setText('sum-stream', res.replace('x', '×') + (fps ? ' · ' + fps + ' fps' : ''));
}

// Resolution cards mirror #sel-resolution and add aspect ratio and field of view.
function renderResolutionCards(status, outputs, available) {
  const host = document.getElementById('res-cards');
  const select = document.getElementById('sel-resolution');
  if (!host || !select) return;
  const selected = select.value;
  const cards = [...available].sort((a, b) => pixelsOf(b) - pixelsOf(a)).map(resolution => {
    const output = outputs.find(item => item.resolution === resolution) || {};
    const maximum = resolutionMaxFps[resolution] ?? output.max_fps ?? output.sensor_max_fps;
    const card = el('button', 'res-card');
    card.type = 'button';
    card.setAttribute('role', 'radio');
    card.setAttribute('aria-checked', String(resolution === selected));
    card.append(el('span', 'res', resolution.replace('x', '×')));
    card.append(el('span', 'fps', maximum != null ? tr('camera.upToFps', { value: Math.floor(maximum) }) : ''));
    const meta = el('span', 'meta');
    if (output.aspect) meta.append(el('span', '', output.aspect));
    if (output.fov_width_pct != null) {
      meta.append(output.fov_width_pct >= 97
        ? el('span', '', tr('camera.mode.fullWidth'))
        : el('span', 'crop', tr('camera.mode.cropped', { value: output.fov_width_pct })));
    }
    if (output.kind === 'scaled') meta.append(el('span', '', tr('camera.mode.scaledFrom', { value: output.sensor_resolution })));
    if (output.limited_by === 'h264_encoder') meta.append(el('span', '', tr('camera.mode.encoderLimit')));
    if (output.source === 'model_fallback') meta.append(el('span', '', tr('camera.mode.fallback')));
    if (meta.childNodes.length) card.append(meta);
    card.addEventListener('click', () => {
      if (select.value === resolution) return;
      select.value = resolution;
      if (typeof select.onchange === 'function') select.onchange();
    });
    return card;
  });
  host.replaceChildren(...cards);
  renderSensorDiagram(status, outputs, selected);
  updateStreamSummary();
}

// Draw which part of the sensor the selected output reads (crop rectangles from rpicam).
function renderSensorDiagram(status, outputs, resolution) {
  const figure = document.getElementById('sensor-diagram');
  if (!figure) return;
  const native = (status.native_sensor_modes || []).filter(mode => Array.isArray(mode.crop));
  const output = outputs.find(item => item.resolution === resolution);
  const mode = output && native.find(item => item.resolution === output.sensor_resolution);
  if (!mode) { figure.classList.add('hidden'); return; }
  const width = Math.max(...native.map(item => item.crop[0] + item.crop[2]));
  const height = Math.max(...native.map(item => item.crop[1] + item.crop[3]));
  const [x, y, w, h] = mode.crop;
  const [ow, oh] = resolution.split('x').map(Number);
  const visibleW = Math.min(w, h * ow / oh);
  const visibleH = Math.min(h, w * oh / ow);
  const svg = document.getElementById('sensor-svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  const full = document.getElementById('sensor-full');
  full.setAttribute('width', width); full.setAttribute('height', height); full.setAttribute('rx', width * 0.015);
  const crop = document.getElementById('sensor-crop');
  crop.setAttribute('x', x + (w - visibleW) / 2); crop.setAttribute('y', y + (h - visibleH) / 2);
  crop.setAttribute('width', visibleW); crop.setAttribute('height', visibleH); crop.setAttribute('rx', width * 0.01);
  const pct = Math.round(100 * visibleW / width);
  setText('sensor-caption', pct >= 97
    ? tr('sensor.full', { width, height })
    : tr('sensor.cropped', { value: pct, width, height }));
  figure.classList.remove('hidden');
}

function renderLiveOverlay() {
  const camera = consoleState.camera;
  const mode = document.getElementById('ov-mode');
  const fov = document.getElementById('ov-fov');
  if (!mode || !fov) return;
  if (!camera || !camera.resolution) { mode.textContent = '—'; fov.classList.add('hidden'); return; }
  const parts = [camera.resolution.replace('x', '×'), camera.framerate + ' fps'];
  const rtsp = consoleState.stream?.rtsp;
  if (rtsp?.running && rtsp.bitrate_kbps) parts.push((rtsp.bitrate_kbps / 1000).toFixed(1) + ' Mbit/s');
  mode.textContent = parts.join(' · ');
  const output = (camera.video_output_modes || camera.output_modes || []).find(item => item.resolution === camera.resolution);
  const pct = output?.fov_width_pct;
  if (pct != null && pct < 97) {
    fov.textContent = tr('camera.mode.cropped', { value: pct });
    fov.classList.remove('hidden');
  } else {
    fov.classList.add('hidden');
  }
}

function throttleNames(flags) {
  const labels = {
    under_voltage: tr('system.throttle.under_voltage'),
    freq_capped: tr('system.throttle.freq_capped'),
    throttled: tr('system.throttle.throttled'),
    soft_temp_limit: tr('system.throttle.soft_temp_limit'),
  };
  return flags.map(flag => labels[flag] || flag).join(', ');
}

function temperatureLevel(celsius) {
  if (celsius == null) return 'info';
  return celsius >= 80 ? 'crit' : celsius >= 70 ? 'warn' : 'ok';
}

// Each check: { level: ok|warn|crit|info, title, value, text }.
function buildChecks() {
  const { camera, stream, system, update } = consoleState;
  const checks = [];
  if (camera) {
    checks.push(camera.running
      ? { level: 'ok', title: tr('checks.camera'), value: [camera.sensor || camera.model, camera.backend].filter(Boolean).join(' · '),
          text: tr('checks.cameraOk', { resolution: (camera.resolution || '—').replace('x', '×'), fps: camera.framerate || '—' }) }
      : { level: 'crit', title: tr('checks.camera'), value: camera.model || '—',
          text: camera.setup_hint ? tr('checks.cameraMissingHint') : tr('checks.cameraStopped') });
  } else {
    checks.push({ level: 'crit', title: tr('checks.camera'), value: '—', text: tr('header.connectionError') });
  }
  if (stream) {
    const rtsp = stream.rtsp || {};
    if (!rtsp.running) {
      checks.push({ level: 'info', title: tr('checks.stream'), value: 'MJPEG', text: tr('checks.streamOff') });
    } else if (!rtsp.publisher_running || rtsp.mediamtx_running === false) {
      checks.push({ level: 'warn', title: tr('checks.stream'), value: 'RTSP', text: tr('checks.streamDown') });
    } else {
      checks.push({ level: 'ok', title: tr('checks.stream'), value: 'RTSP' + (rtsp.hls_port ? ' · HLS' : ''),
        text: tr('checks.streamOk', { bitrate: ((rtsp.bitrate_kbps || 0) / 1000).toFixed(1) }) });
    }
    const mjpeg = stream.mjpeg || {};
    checks.push({ level: mjpeg.running === false ? 'warn' : 'ok', title: tr('checks.preview'),
      value: tr(mjpeg.clients === 1 ? 'checks.clientsOne' : 'checks.clientsMany', { count: mjpeg.clients || 0 }),
      text: mjpeg.actual_fps != null ? tr('checks.previewFps', { value: mjpeg.actual_fps }) : tr('checks.previewIdle') });
  }
  if (system) {
    const temp = system.cpu_temp_c;
    if (temp != null) {
      checks.push({ level: temperatureLevel(temp), title: tr('checks.temperature'), value: temp.toFixed(1) + ' °C',
        text: tr(temp >= 80 ? 'checks.temperatureCrit' : temp >= 70 ? 'checks.temperatureWarn' : 'checks.temperatureOk') });
    }
    const throttling = system.throttling;
    if (throttling) {
      if (throttling.active.length) {
        checks.push({ level: 'crit', title: tr('checks.power'), value: throttling.raw,
          text: tr('checks.powerNow', { value: throttleNames(throttling.active) }) + ' ' + tr('checks.powerAdvice') });
      } else if (throttling.since_boot.length) {
        checks.push({ level: 'warn', title: tr('checks.power'), value: throttling.raw,
          text: tr('checks.powerSinceBoot', { value: throttleNames(throttling.since_boot) }) + ' ' + tr('checks.powerAdvice') });
      } else {
        checks.push({ level: 'ok', title: tr('checks.power'), value: throttling.raw, text: tr('checks.powerOk') });
      }
    }
    if (system.disk) {
      const { free_gb: free, total_gb: total } = system.disk;
      const low = free < 1 || (total > 0 && free / total < 0.1);
      checks.push({ level: low ? 'warn' : 'ok', title: tr('checks.storage'), value: free.toFixed(1) + ' GB',
        text: tr(low ? 'checks.storageLow' : 'checks.storageOk', { free: free.toFixed(1), total: total.toFixed(1) }) });
    }
  }
  if (update) {
    if (update.state === 'error') {
      checks.push({ level: 'warn', title: tr('checks.software'), value: 'v' + (update.current_version || '?'), text: update.error || tr('update.unknownError') });
    } else if (update.update_available) {
      checks.push({ level: 'info', title: tr('checks.software'), value: 'v' + update.current_version,
        text: tr('checks.updateAvailable', { version: update.latest_version }) });
    } else {
      checks.push({ level: 'ok', title: tr('checks.software'), value: update.current_version ? 'v' + update.current_version : '—',
        text: update.state === 'up_to_date' ? tr('update.upToDateMessage') : tr('checks.updateUnknown') });
    }
  }
  return checks;
}

function renderChecks(checks) {
  const host = document.getElementById('checks');
  if (!host) return;
  const iconFor = { ok: 'ok', warn: 'warn', crit: 'crit', info: 'info' };
  host.replaceChildren(...checks.map(check => {
    const row = el('div', 'check ' + check.level);
    row.append(icon(iconFor[check.level]), el('b', '', check.title), el('span', 'val', check.value || ''), el('p', '', check.text || ''));
    return row;
  }));
  const ok = checks.filter(check => check.level === 'ok' || check.level === 'info').length;
  setText('checks-summary', tr('checks.summary', { ok, total: checks.length }));
  const problems = checks.filter(check => check.level === 'warn' || check.level === 'crit');
  const badge = document.getElementById('tab-system-badge');
  if (badge) {
    badge.textContent = problems.length;
    badge.classList.toggle('hidden', problems.length === 0);
    badge.classList.toggle('crit', problems.some(check => check.level === 'crit'));
  }
}

function renderHealthChips() {
  const { camera, system } = consoleState;
  const cameraChip = document.getElementById('hc-camera');
  if (cameraChip) cameraChip.className = 'hchip' + (camera && camera.running ? '' : ' crit');
  const tempChip = document.getElementById('hc-temp');
  if (tempChip) {
    const temp = system?.cpu_temp_c;
    tempChip.classList.toggle('hidden', temp == null);
    if (temp != null) {
      const level = temperatureLevel(temp);
      tempChip.className = 'hchip ' + level;
      setText('hc-temp-text', temp.toFixed(1) + ' °C');
    }
  }
  const powerChip = document.getElementById('hc-power');
  const throttling = system?.throttling;
  if (powerChip) {
    const active = throttling?.active?.length;
    const sinceBoot = throttling?.since_boot?.length;
    powerChip.classList.toggle('hidden', !active && !sinceBoot);
    powerChip.className = 'hchip ' + (active ? 'crit' : 'warn') + (!active && !sinceBoot ? ' hidden' : '');
    if (active) setText('hc-power-text', tr('checks.powerChipNow', { value: throttleNames(throttling.active) }));
    else if (sinceBoot) setText('hc-power-text', tr('checks.powerChipBoot', { value: throttleNames(throttling.since_boot) }));
  }
}

function renderConsole() {
  renderHealthChips();
  renderChecks(buildChecks());
  renderLiveOverlay();
}

// ── Recordings: film strip and day-grouped timeline ──────────────────────
function snapshotUrl(item) {
  return '/snapshots/' + encodeURIComponent(item.filename);
}

function renderSnapshots() {
  const film = document.getElementById('snapshot-film');
  if (film) {
    const recent = snapshotItems.slice(-12).reverse();
    if (!recent.length) {
      film.replaceChildren(el('span', 'film-empty', tr('snapshot.none')));
    } else {
      film.replaceChildren(...recent.map(item => {
        const link = el('a', item.filename === newestSnapshot ? 'new' : '');
        link.href = snapshotUrl(item);
        link.target = '_blank';
        link.rel = 'noopener';
        link.title = item.filename;
        const img = el('img');
        img.loading = 'lazy';
        img.alt = tr('recordings.snapshot') + ' ' + item.filename;
        img.src = snapshotUrl(item);
        link.append(img);
        return link;
      }));
    }
  }
  renderTimeline();
}

function dayLabel(date) {
  const language = window.BambuCamI18n ? window.BambuCamI18n.getLanguage() : 'en';
  const today = new Date();
  const startOf = value => new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const days = Math.round((startOf(today) - startOf(date)) / 86400000);
  if (days === 0) return tr('recordings.today');
  if (days === 1) return tr('recordings.yesterday');
  return date.toLocaleDateString(language, { weekday: 'long', day: 'numeric', month: 'long' });
}

function timelineEntries() {
  const entries = [];
  if (timelineFilter !== 'timelapse') {
    snapshotItems.forEach(item => entries.push({ type: 'snapshot', at: (item.created || 0) * 1000, item }));
  }
  if (timelineFilter !== 'snapshot') {
    timelapseSessions.forEach(item => entries.push({ type: 'timelapse', at: (item.started_at || 0) * 1000, item }));
  }
  return entries.sort((a, b) => b.at - a.at);
}

function timelineRow(entry) {
  const language = window.BambuCamI18n ? window.BambuCamI18n.getLanguage() : 'en';
  const row = el('div', 'card entry');
  const time = el('time', '', new Date(entry.at).toLocaleTimeString(language, { hour: '2-digit', minute: '2-digit' }));
  time.dateTime = new Date(entry.at).toISOString();
  const thumb = el('div', 'thumb');
  const body = el('div');
  const actions = el('div', 'actions');
  const linkButton = (label, href, download) => {
    const link = el('a', 'btn small', label);
    link.href = href;
    if (download) link.setAttribute('download', '');
    else { link.target = '_blank'; link.rel = 'noopener'; }
    return link;
  };
  if (entry.type === 'snapshot') {
    const item = entry.item;
    if (item.filename === newestSnapshot) row.classList.add('new');
    const img = el('img');
    img.loading = 'lazy';
    img.alt = '';
    img.src = snapshotUrl(item);
    thumb.append(img);
    const kind = el('div', 'kind');
    kind.append(icon('camera'), document.createTextNode(item.size_kb ? item.size_kb + ' KB' : ''));
    body.append(el('div', 'title', tr('recordings.snapshot')), kind);
    actions.append(linkButton(tr('recordings.open'), snapshotUrl(item)), linkButton('↓', snapshotUrl(item), true));
  } else {
    const item = entry.item;
    const running = timelapseStatus && timelapseStatus.running && timelapseStatus.session_id === item.session_id;
    thumb.append(icon('film'));
    const kind = el('div', 'kind');
    const details = [tr('timelapse.frames', { count: item.frame_count })];
    if (item.video_available) details.push((item.video_bytes / 1048576).toFixed(1) + ' MB');
    else if (!running) details.push(tr('recordings.notRendered'));
    kind.append(icon('film'), document.createTextNode(details.join(' · ')));
    const title = el('div', 'title', item.title || item.session_id);
    if (running) title.append(document.createTextNode(' '), el('span', 'chip ok', tr('timelapse.stateRunning')));
    body.append(title, kind);
    const id = encodeURIComponent(item.session_id);
    if (item.video_available) {
      actions.append(linkButton(tr('recordings.play'), `/api/v1/timelapse/${id}/video`));
    } else if (!running) {
      const render = el('button', 'btn small', tr('timelapse.render'));
      render.type = 'button';
      render.addEventListener('click', () => window.BambuCamTimelapse?.render(item.session_id));
      actions.append(render);
    }
    if (!running) {
      const remove = el('button', 'btn small danger', tr('timelapse.delete'));
      remove.type = 'button';
      remove.addEventListener('click', () => window.BambuCamTimelapse?.remove(item.session_id));
      actions.append(remove);
    }
  }
  row.append(time, thumb, body, actions);
  return row;
}

function renderTimeline() {
  const host = document.getElementById('timeline');
  if (!host) return;
  const entries = timelineEntries();
  setText('rec-summary', tr('recordings.summary', { snapshots: snapshotItems.length, timelapses: timelapseSessions.length }));
  if (!entries.length) {
    host.replaceChildren(el('div', 'card timeline-empty', tr('recordings.empty')));
    toggleRow('timeline-more', false);
    return;
  }
  const groups = new Map();
  entries.slice(0, timelineLimit).forEach(entry => {
    const label = dayLabel(new Date(entry.at));
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(entry);
  });
  host.replaceChildren(...[...groups].map(([label, list]) => {
    const day = el('section', 'day');
    day.append(el('h2', 'day-label', label), ...list.map(timelineRow));
    return day;
  }));
  toggleRow('timeline-more', entries.length > timelineLimit);
}

function showMoreTimeline() {
  timelineLimit += 40;
  renderTimeline();
}

window.addEventListener('bambucam:timelapsesessions', event => {
  timelapseSessions = event.detail.sessions || [];
  timelapseStatus = event.detail.status || null;
  renderTimeline();
});

(function initConsole() {
  document.querySelectorAll('.tabs button[data-view]').forEach(button => {
    button.addEventListener('click', () => showView(button.dataset.view));
  });
  document.querySelectorAll('[data-goto]').forEach(button => {
    button.addEventListener('click', () => showView(button.dataset.goto));
  });
  document.querySelectorAll('[data-theme-set]').forEach(button => {
    button.addEventListener('click', () => setTheme(button.dataset.themeSet));
  });
  document.querySelectorAll('#rec-filter button[data-filter]').forEach(button => {
    button.addEventListener('click', () => {
      timelineFilter = button.dataset.filter;
      timelineLimit = 40;
      document.querySelectorAll('#rec-filter button').forEach(other => {
        other.setAttribute('aria-pressed', String(other === button));
      });
      renderTimeline();
    });
  });
  window.addEventListener('hashchange', () => showView(location.hash.slice(1)));
  let savedTheme = 'auto';
  try { savedTheme = localStorage.getItem(THEME_KEY) || 'auto'; } catch (_) { /* default */ }
  applyTheme(savedTheme);
  showView(location.hash.slice(1));
  consoleReady = true;
  window.showToast = toast;
  window.BambuCamConsole = { go: showView };
})();

// ── Boot ────────────────────────────────────────────────────────────────────
init();
