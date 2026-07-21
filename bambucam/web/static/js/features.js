"use strict";

(function () {
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
        <div class="card-header"><span class="card-title">Kamera-Profil</span></div>
        <div class="card-body">
          <div class="field">
            <div class="field-label"><span>Profil</span></div>
            <select id="feature-profile-select"></select>
          </div>
          <p class="hint feature-description" id="feature-profile-description"></p>
          <div class="btn-row">
            <button class="btn btn-secondary" id="feature-profile-apply">Profil anwenden</button>
          </div>
        </div>
      </div>
      <div class="card feature-card" id="timelapse-card">
        <div class="card-header">
          <span class="card-title">Timelapse</span>
          <span class="feature-state" id="timelapse-state">Bereit</span>
        </div>
        <div class="card-body">
          <div class="field">
            <div class="field-label"><span>Titel</span></div>
            <input class="text-input" id="timelapse-title" placeholder="Druckauftrag" maxlength="160" />
          </div>
          <div class="feature-grid">
            <div class="field">
              <div class="field-label"><span>Intervall (s)</span></div>
              <input class="text-input" type="number" id="timelapse-interval" min="0.5" max="86400" step="0.5" value="10" />
            </div>
            <div class="field">
              <div class="field-label"><span>Video-FPS</span></div>
              <input class="text-input" type="number" id="timelapse-fps" min="1" max="120" value="30" />
            </div>
          </div>
          <div class="feature-progress" id="timelapse-progress">Noch keine Sitzung</div>
          <div class="btn-row">
            <button class="btn btn-secondary" id="timelapse-start">Starten</button>
            <button class="btn btn-danger" id="timelapse-stop" disabled>Stoppen & rendern</button>
          </div>
          <div class="snapshot-list feature-sessions" id="timelapse-sessions"></div>
        </div>
      </div>`;
  }

  let profiles = [];
  let recommendedProfile = "";

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
        const recommended = profile.name === recommendedProfile ? " (Empfohlen)" : "";
        const selected = profile.name === selectedProfile ? "selected" : "";
        return `<option value="${profile.name}" ${selected}>${profile.label}${recommended}</option>`;
      })
      .join("");
    updateProfileDescription();
  }

  function updateProfileDescription() {
    const name = document.getElementById("feature-profile-select").value;
    const profile = profiles.find((item) => item.name === name);
    document.getElementById("feature-profile-description").textContent = profile
      ? `${profile.description} ${profile.resolved.resolution} @ ${profile.resolved.framerate} FPS${profile.name === recommendedProfile ? " — für dieses System empfohlen" : ""}`
      : "";
  }

  async function applyProfile() {
    const name = document.getElementById("feature-profile-select").value;
    const button = document.getElementById("feature-profile-apply");
    button.disabled = true;
    try {
      const payload = await request(`/api/v1/camera/profiles/${encodeURIComponent(name)}`, { method: "POST" });
      toast(`${payload.profile.label}: ${payload.profile.resolution} @ ${payload.profile.framerate} FPS`);
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
      toast("Timelapse gestartet");
      await refreshTimelapse();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function stopTimelapse() {
    const button = document.getElementById("timelapse-stop");
    button.disabled = true;
    button.textContent = "Rendere…";
    try {
      await request("/api/v1/timelapse/stop", {
        method: "POST",
        body: JSON.stringify({ render: true }),
      });
      toast("Timelapse beendet und gerendert");
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.textContent = "Stoppen & rendern";
      await refreshTimelapse();
    }
  }

  async function renderSession(sessionId) {
    try {
      await request(`/api/v1/timelapse/${encodeURIComponent(sessionId)}/render`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      toast("Timelapse gerendert");
      await refreshTimelapse();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function deleteSession(sessionId) {
    if (!window.confirm("Timelapse-Sitzung wirklich löschen?")) return;
    try {
      await request(`/api/v1/timelapse/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
      toast("Timelapse gelöscht");
      await refreshTimelapse();
    } catch (error) {
      toast(error.message, true);
    }
  }

  function renderSessions(sessions) {
    const container = document.getElementById("timelapse-sessions");
    if (!sessions.length) {
      container.innerHTML = '<div class="hint">Noch keine Timelapse-Sitzungen.</div>';
      return;
    }
    container.innerHTML = sessions.slice(0, 8).map((session) => {
      const title = session.title || session.session_id;
      const video = session.video_available
        ? `<a class="feature-link" href="/api/v1/timelapse/${encodeURIComponent(session.session_id)}/video">MP4 ↓</a>`
        : `<button class="feature-link-button" data-render="${session.session_id}">Rendern</button>`;
      return `<div class="feature-session">
        <div><strong>${title}</strong><small>${session.frame_count} Frames</small></div>
        <div>${video}<button class="feature-link-button danger" data-delete="${session.session_id}">Löschen</button></div>
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
        ? "Rendert"
        : status.running ? "Läuft" : "Bereit";
      document.getElementById("timelapse-progress").textContent = status.session_id
        ? `${status.frame_count} Frames · ${status.session_id}${status.error ? ` · ${status.error}` : ""}`
        : "Noch keine Sitzung";
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
    document.getElementById("feature-profile-select").addEventListener("change", updateProfileDescription);
    document.getElementById("feature-profile-apply").addEventListener("click", applyProfile);
    document.getElementById("timelapse-start").addEventListener("click", startTimelapse);
    document.getElementById("timelapse-stop").addEventListener("click", stopTimelapse);
    loadProfiles().catch((error) => toast(error.message, true));
    refreshTimelapse();
    window.setInterval(refreshTimelapse, 5000);
  }

  window.addEventListener("DOMContentLoaded", initialise);
})();
