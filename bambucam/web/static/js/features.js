"use strict";

(function () {
  function tr(key, values) {
    return window.BambuCamI18n ? window.BambuCamI18n.t(key, values) : key;
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  }

  function toast(message, isError = false) {
    if (typeof window.showToast === "function") {
      window.showToast(message, isError ? "error" : "success");
      return;
    }
    console[isError ? "error" : "log"](message);
  }

  // Profile picker inside the "Image settings" group of the inspector.
  function profileMarkup() {
    return `
      <div class="field" id="camera-profiles-card">
        <label class="field-label" for="feature-profile-select" data-i18n="profile.title">Camera profile</label>
        <div class="row" style="flex-wrap:nowrap">
          <select id="feature-profile-select"></select>
          <button class="btn" id="feature-profile-apply" data-i18n="profile.apply">Apply profile</button>
        </div>
        <p class="field-hint" id="feature-profile-description"></p>
      </div>`;
  }

  // Timelapse controls on the Recordings tab.
  function timelapseMarkup() {
    return `
      <div class="card pad" id="timelapse-card">
        <div class="card-head">
          <h2 data-i18n="timelapse.title">Timelapse</h2>
          <span class="chip neutral" id="timelapse-state" data-i18n="timelapse.ready">Ready</span>
        </div>
        <div class="tl-form">
          <div class="field">
            <label class="field-label" for="timelapse-title" data-i18n="timelapse.jobTitle">Title</label>
            <input class="text-input" id="timelapse-title" placeholder="Print job" data-i18n-placeholder="timelapse.jobPlaceholder" maxlength="160" />
          </div>
          <div class="field">
            <label class="field-label" for="timelapse-interval" data-i18n="timelapse.interval">Interval (s)</label>
            <input class="text-input" type="number" id="timelapse-interval" min="0.5" max="86400" step="0.5" value="10" />
          </div>
          <div class="field">
            <label class="field-label" for="timelapse-fps" data-i18n="timelapse.videoFps">Video FPS</label>
            <input class="text-input" type="number" id="timelapse-fps" min="1" max="120" value="30" />
          </div>
        </div>
        <p class="muted small" id="timelapse-progress" data-i18n="timelapse.noSession">No session yet</p>
        <div class="row">
          <button class="btn primary" id="timelapse-start" data-i18n="timelapse.start">Start</button>
          <button class="btn danger" id="timelapse-stop" disabled data-i18n="timelapse.stopRender">Stop & render</button>
        </div>
      </div>`;
  }

  // Compact timelapse status under the live view.
  function miniMarkup() {
    return `
      <div class="card-head">
        <h2 data-i18n="timelapse.title">Timelapse</h2>
        <span class="chip neutral" id="timelapse-mini-state" data-i18n="timelapse.ready">Ready</span>
      </div>
      <div class="tl-mini-nums">
        <div><span class="field-label" data-i18n="timelapse.framesLabel">Frames</span><b class="num" id="timelapse-mini-frames">–</b></div>
        <div><span class="field-label" data-i18n="timelapse.nextFrame">Next frame</span><b class="num" id="timelapse-mini-next">–</b></div>
      </div>
      <div class="progress-track"><div class="progress-fill" id="timelapse-mini-bar" style="width:0%"></div></div>
      <div class="row">
        <button class="btn" id="timelapse-mini-toggle" data-i18n="timelapse.start">Start</button>
        <button class="btn ghost small" data-goto="recordings" data-i18n="timelapse.manage">Manage</button>
      </div>`;
  }

  let profiles = [];
  let recommendedProfile = "";
  let activeProfile = "custom";
  let lastStatus = null;
  let lastSessions = [];

  function profileText(profile, field) {
    const key = `profile.${profile.name}.${field}`;
    const translated = tr(key);
    return translated === key ? profile[field] : translated;
  }

  function renderProfileOptions(selectedProfile = activeProfile) {
    const select = document.getElementById("feature-profile-select");
    const customOption = activeProfile === "custom"
      ? `<option value="custom"${selectedProfile === "custom" ? " selected" : ""}>${tr('profile.custom')}</option>`
      : "";
    const profileOptions = profiles.map((profile) => {
      const recommended = profile.name === recommendedProfile ? ` (${tr('profile.recommended')})` : "";
      const selected = profile.name === selectedProfile ? " selected" : "";
      return `<option value="${profile.name}"${selected}>${profileText(profile, 'label')}${recommended}</option>`;
    }).join("");
    select.innerHTML = customOption + profileOptions;
    updateProfileDescription();
    const summary = document.getElementById("sum-image");
    const active = profiles.find((item) => item.name === activeProfile);
    if (summary) summary.textContent = active ? profileText(active, 'label') : tr('profile.custom');
  }

  async function loadProfiles() {
    const payload = await request("/api/v1/camera/profiles");
    profiles = payload.profiles || [];
    recommendedProfile = payload.recommended || "";
    activeProfile = payload.active || "custom";
    renderProfileOptions();
  }

  function updateProfileDescription() {
    const name = document.getElementById("feature-profile-select").value;
    const profile = profiles.find((item) => item.name === name);
    document.getElementById("feature-profile-apply").disabled = name === "custom";
    document.getElementById("feature-profile-description").textContent = name === "custom"
      ? tr("profile.customDescription")
      : profile
        ? `${profileText(profile, 'description')} ${profile.resolved.resolution} @ ${profile.resolved.framerate} FPS${profile.name === recommendedProfile ? ` ${tr('profile.recommendedSuffix')}` : ""}`
        : "";
  }

  async function applyProfile() {
    const name = document.getElementById("feature-profile-select").value;
    if (name === "custom") return;
    const button = document.getElementById("feature-profile-apply");
    button.disabled = true;
    try {
      const payload = await request(`/api/v1/camera/profiles/${encodeURIComponent(name)}`, { method: "POST" });
      toast(`${profileText({ ...payload.profile, name }, 'label')}: ${payload.profile.resolution} @ ${payload.profile.framerate} FPS`);
      window.dispatchEvent(new CustomEvent("bambucam:configurationchange"));
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function startTimelapse() {
    try {
      await request("/api/v1/timelapse/start", {
        method: "POST",
        body: JSON.stringify({
          title: document.getElementById("timelapse-title").value,
          interval_seconds: Number(document.getElementById("timelapse-interval").value),
          output_fps: Number(document.getElementById("timelapse-fps").value),
        }),
      });
      toast(tr("timelapse.started"));
      await refreshTimelapse();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function stopTimelapse() {
    const buttons = [document.getElementById("timelapse-stop"), document.getElementById("timelapse-mini-toggle")];
    buttons.forEach((button) => { button.disabled = true; button.textContent = tr("timelapse.rendering"); });
    try {
      await request("/api/v1/timelapse/stop", {
        method: "POST",
        body: JSON.stringify({ render: true }),
      });
      toast(tr("timelapse.finished"));
    } catch (error) {
      toast(error.message, true);
    } finally {
      await refreshTimelapse();
    }
  }

  async function renderSession(sessionId) {
    try {
      await request(`/api/v1/timelapse/${encodeURIComponent(sessionId)}/render`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      toast(tr("timelapse.rendered"));
      await refreshTimelapse();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function deleteSession(sessionId) {
    if (!window.confirm(tr("timelapse.deleteConfirm"))) return;
    try {
      await request(`/api/v1/timelapse/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
      toast(tr("timelapse.deleted"));
      await refreshTimelapse();
    } catch (error) {
      toast(error.message, true);
    }
  }

  function setState(element, status) {
    element.textContent = status.rendering
      ? tr("timelapse.stateRendering")
      : status.running ? tr("timelapse.stateRunning") : tr("timelapse.ready");
    element.className = "chip " + (status.running ? "ok" : status.rendering ? "warn" : "neutral");
  }

  // The countdown is derived from next_frame_at so it keeps ticking between polls.
  function renderCountdown() {
    const status = lastStatus;
    const next = document.getElementById("timelapse-mini-next");
    const bar = document.getElementById("timelapse-mini-bar");
    if (!status || !next || !bar) return;
    if (!status.running || !status.next_frame_at) {
      next.textContent = "–";
      bar.style.width = status.running ? "0%" : "0%";
      return;
    }
    const remaining = Math.max(0, status.next_frame_at - Date.now() / 1000);
    next.textContent = `${Math.ceil(remaining)} s`;
    const interval = Number(status.interval_seconds) || 1;
    bar.style.width = `${Math.min(100, Math.max(0, (1 - remaining / interval) * 100))}%`;
  }

  function renderStatus(payload) {
    const status = payload.status;
    lastStatus = status;
    if (!status.running) {
      document.getElementById("timelapse-interval").value = payload.defaults.interval_seconds;
      document.getElementById("timelapse-fps").value = payload.defaults.output_fps;
    } else {
      document.getElementById("timelapse-interval").value = status.interval_seconds;
      document.getElementById("timelapse-fps").value = status.output_fps;
    }
    setState(document.getElementById("timelapse-state"), status);
    setState(document.getElementById("timelapse-mini-state"), status);
    document.getElementById("timelapse-progress").textContent = status.session_id
      ? `${status.title || status.session_id} · ${tr('timelapse.frames', { count: status.frame_count })}${status.error ? ` · ${status.error}` : ""}`
      : tr("timelapse.noSession");
    document.getElementById("timelapse-mini-frames").textContent = status.session_id ? status.frame_count : "–";
    const busy = status.running || status.rendering;
    document.getElementById("timelapse-start").disabled = busy;
    const stop = document.getElementById("timelapse-stop");
    stop.disabled = !status.running || status.rendering;
    stop.textContent = status.rendering ? tr("timelapse.rendering") : tr("timelapse.stopRender");
    const mini = document.getElementById("timelapse-mini-toggle");
    mini.disabled = status.rendering;
    mini.textContent = status.rendering ? tr("timelapse.rendering") : status.running ? tr("timelapse.stopRender") : tr("timelapse.start");
    mini.className = status.running ? "btn danger" : "btn";
    renderCountdown();
  }

  async function refreshTimelapse() {
    try {
      const [payload, sessions] = await Promise.all([
        request("/api/v1/timelapse/status"),
        request("/api/v1/timelapse/sessions"),
      ]);
      renderStatus(payload);
      lastSessions = sessions;
      // app.js merges sessions with snapshots into the recordings timeline.
      window.dispatchEvent(new CustomEvent("bambucam:timelapsesessions", {
        detail: { sessions, status: payload.status },
      }));
    } catch (error) {
      document.getElementById("timelapse-progress").textContent = error.message;
    }
  }

  function initialise() {
    const profileSlot = document.getElementById("profile-slot");
    const timelapseSlot = document.getElementById("timelapse-slot");
    const miniSlot = document.getElementById("timelapse-mini");
    if (!profileSlot || !timelapseSlot || !miniSlot || document.getElementById("timelapse-card")) return;
    profileSlot.innerHTML = profileMarkup();
    timelapseSlot.innerHTML = timelapseMarkup();
    miniSlot.innerHTML = miniMarkup();
    if (window.BambuCamI18n) {
      [profileSlot, timelapseSlot, miniSlot].forEach((slot) => window.BambuCamI18n.translateDocument(slot));
    }
    document.getElementById("feature-profile-select").addEventListener("change", updateProfileDescription);
    document.getElementById("feature-profile-apply").addEventListener("click", applyProfile);
    document.getElementById("timelapse-start").addEventListener("click", startTimelapse);
    document.getElementById("timelapse-stop").addEventListener("click", stopTimelapse);
    document.getElementById("timelapse-mini-toggle").addEventListener("click", () => {
      if (lastStatus && lastStatus.running) stopTimelapse();
      else startTimelapse();
    });
    miniSlot.querySelectorAll("[data-goto]").forEach((button) => {
      button.addEventListener("click", () => window.BambuCamConsole && window.BambuCamConsole.go(button.dataset.goto));
    });
    window.BambuCamTimelapse = { render: renderSession, remove: deleteSession, sessions: () => lastSessions };
    loadProfiles().catch((error) => toast(error.message, true));
    refreshTimelapse();
    window.setInterval(refreshTimelapse, 5000);
    window.setInterval(renderCountdown, 1000);
  }

  window.addEventListener("DOMContentLoaded", initialise);
  window.addEventListener("bambucam:languagechange", () => {
    if (!document.getElementById("timelapse-card")) return;
    if (profiles.length) {
      const selected = document.getElementById("feature-profile-select").value;
      renderProfileOptions(selected);
    }
    refreshTimelapse();
  });
  window.addEventListener("bambucam:configurationchange", () => {
    if (!document.getElementById("camera-profiles-card")) return;
    loadProfiles().catch((error) => toast(error.message, true));
  });
})();
