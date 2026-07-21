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

  function cardMarkup() {
    return `
      <div class="card feature-card" id="camera-profiles-card">
        <div class="card-header"><span class="card-title" data-i18n="profile.title">Camera profile</span></div>
        <div class="card-body">
          <div class="field">
            <div class="field-label"><span data-i18n="profile.field">Profile</span></div>
            <select id="feature-profile-select"></select>
          </div>
          <p class="hint feature-description" id="feature-profile-description"></p>
          <div class="btn-row">
            <button class="btn btn-secondary" id="feature-profile-apply" data-i18n="profile.apply">Apply profile</button>
          </div>
        </div>
      </div>
      <div class="card feature-card" id="timelapse-card">
        <div class="card-header">
          <span class="card-title" data-i18n="timelapse.title">Timelapse</span>
          <span class="feature-state" id="timelapse-state" data-i18n="timelapse.ready">Ready</span>
        </div>
        <div class="card-body">
          <div class="field">
            <div class="field-label"><span data-i18n="timelapse.jobTitle">Title</span></div>
            <input class="text-input" id="timelapse-title" placeholder="Print job" data-i18n-placeholder="timelapse.jobPlaceholder" maxlength="160" />
          </div>
          <div class="feature-grid">
            <div class="field">
              <div class="field-label"><span data-i18n="timelapse.interval">Interval (s)</span></div>
              <input class="text-input" type="number" id="timelapse-interval" min="0.5" max="86400" step="0.5" value="10" />
            </div>
            <div class="field">
              <div class="field-label"><span data-i18n="timelapse.videoFps">Video FPS</span></div>
              <input class="text-input" type="number" id="timelapse-fps" min="1" max="120" value="30" />
            </div>
          </div>
          <div class="feature-progress" id="timelapse-progress" data-i18n="timelapse.noSession">No session yet</div>
          <div class="btn-row">
            <button class="btn btn-secondary" id="timelapse-start" data-i18n="timelapse.start">Start</button>
            <button class="btn btn-danger" id="timelapse-stop" disabled data-i18n="timelapse.stopRender">Stop & render</button>
          </div>
          <div class="snapshot-list feature-sessions" id="timelapse-sessions"></div>
        </div>
      </div>`;
  }

  let profiles = [];
  let recommendedProfile = "";
  let lastSessions = [];

  function profileText(profile, field) {
    const key = `profile.${profile.name}.${field}`;
    const translated = tr(key);
    return translated === key ? profile[field] : translated;
  }

  async function loadProfiles() {
    const payload = await request("/api/v1/camera/profiles");
    profiles = payload.profiles || [];
    recommendedProfile = payload.recommended || "";
    const selectedProfile = payload.active && payload.active !== "custom"
      ? payload.active
      : recommendedProfile;
    const select = document.getElementById("feature-profile-select");
    select.innerHTML = profiles
      .map((profile) => {
        const recommended = profile.name === recommendedProfile ? ` (${tr('profile.recommended')})` : "";
        const selected = profile.name === selectedProfile ? "selected" : "";
        return `<option value="${profile.name}" ${selected}>${profileText(profile, 'label')}${recommended}</option>`;
      })
      .join("");
    updateProfileDescription();
  }

  function updateProfileDescription() {
    const name = document.getElementById("feature-profile-select").value;
    const profile = profiles.find((item) => item.name === name);
    document.getElementById("feature-profile-description").textContent = profile
      ? `${profileText(profile, 'description')} ${profile.resolved.resolution} @ ${profile.resolved.framerate} FPS${profile.name === recommendedProfile ? ` ${tr('profile.recommendedSuffix')}` : ""}`
      : "";
  }

  async function applyProfile() {
    const name = document.getElementById("feature-profile-select").value;
    const button = document.getElementById("feature-profile-apply");
    button.disabled = true;
    try {
      const payload = await request(`/api/v1/camera/profiles/${encodeURIComponent(name)}`, { method: "POST" });
      toast(`${profileText({ ...payload.profile, name }, 'label')}: ${payload.profile.resolution} @ ${payload.profile.framerate} FPS`);
      await loadProfiles();
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
    const button = document.getElementById("timelapse-stop");
    button.disabled = true;
    button.textContent = tr("timelapse.rendering");
    try {
      await request("/api/v1/timelapse/stop", {
        method: "POST",
        body: JSON.stringify({ render: true }),
      });
      toast(tr("timelapse.finished"));
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.textContent = tr("timelapse.stopRender");
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

  function renderSessions(sessions) {
    lastSessions = sessions;
    const container = document.getElementById("timelapse-sessions");
    if (!sessions.length) {
      container.innerHTML = `<div class="hint">${tr('timelapse.noSessions')}</div>`;
      return;
    }
    container.innerHTML = sessions.slice(0, 8).map((session) => {
      const title = session.title || session.session_id;
      const video = session.video_available
        ? `<a class="feature-link" href="/api/v1/timelapse/${encodeURIComponent(session.session_id)}/video">MP4 ↓</a>`
        : `<button class="feature-link-button" data-render="${session.session_id}">${tr('timelapse.render')}</button>`;
      return `<div class="feature-session">
        <div><strong>${title}</strong><small>${tr('timelapse.frames', { count: session.frame_count })}</small></div>
        <div>${video}<button class="feature-link-button danger" data-delete="${session.session_id}">${tr('timelapse.delete')}</button></div>
      </div>`;
    }).join("");
    container.querySelectorAll("[data-render]").forEach((button) => {
      button.addEventListener("click", () => renderSession(button.dataset.render));
    });
    container.querySelectorAll("[data-delete]").forEach((button) => {
      button.addEventListener("click", () => deleteSession(button.dataset.delete));
    });
  }

  async function refreshTimelapse() {
    try {
      const [payload, sessions] = await Promise.all([
        request("/api/v1/timelapse/status"),
        request("/api/v1/timelapse/sessions"),
      ]);
      const status = payload.status;
      document.getElementById("timelapse-interval").value = status.running
        ? status.interval_seconds
        : payload.defaults.interval_seconds;
      document.getElementById("timelapse-fps").value = status.running
        ? status.output_fps
        : payload.defaults.output_fps;
      document.getElementById("timelapse-state").textContent = status.rendering
        ? tr("timelapse.stateRendering")
        : status.running ? tr("timelapse.stateRunning") : tr("timelapse.ready");
      document.getElementById("timelapse-progress").textContent = status.session_id
        ? `${tr('timelapse.frames', { count: status.frame_count })} · ${status.session_id}${status.error ? ` · ${status.error}` : ""}`
        : tr("timelapse.noSession");
      document.getElementById("timelapse-start").disabled = status.running || status.rendering;
      document.getElementById("timelapse-stop").disabled = !status.running || status.rendering;
      renderSessions(sessions);
    } catch (error) {
      document.getElementById("timelapse-progress").textContent = error.message;
    }
  }

  function initialise() {
    const aside = document.querySelector("aside");
    if (!aside || document.getElementById("timelapse-card")) return;
    aside.insertAdjacentHTML("beforeend", cardMarkup());
    if (window.BambuCamI18n) window.BambuCamI18n.translateDocument(aside);
    document.getElementById("feature-profile-select").addEventListener("change", updateProfileDescription);
    document.getElementById("feature-profile-apply").addEventListener("click", applyProfile);
    document.getElementById("timelapse-start").addEventListener("click", startTimelapse);
    document.getElementById("timelapse-stop").addEventListener("click", stopTimelapse);
    loadProfiles().catch((error) => toast(error.message, true));
    refreshTimelapse();
    window.setInterval(refreshTimelapse, 5000);
  }

  window.addEventListener("DOMContentLoaded", initialise);
  window.addEventListener("bambucam:languagechange", () => {
    if (!document.getElementById("timelapse-card")) return;
    if (profiles.length) {
      const selected = document.getElementById("feature-profile-select").value;
      const select = document.getElementById("feature-profile-select");
      select.innerHTML = profiles.map((profile) => {
        const recommended = profile.name === recommendedProfile ? ` (${tr('profile.recommended')})` : "";
        return `<option value="${profile.name}"${profile.name === selected ? " selected" : ""}>${profileText(profile, 'label')}${recommended}</option>`;
      }).join("");
      updateProfileDescription();
    }
    renderSessions(lastSessions);
    refreshTimelapse();
  });
})();
