"use strict";

(function () {
  const STORAGE_KEY = "bambucam.language";
  const SUPPORTED_LANGUAGES = ["en", "de"];

  const translations = {
    en: {
      "language.label": "Language",
      "common.apply": "Apply",
      "common.save": "Save",
      "common.loading": "Loading…",
      "common.wait": "Please wait…",
      "common.error": "Error: {message}",
      "common.close": "Close",
      "common.current": "Current",
      "common.latest": "Latest",
      "common.install": "Install",
      "common.delete": "Delete",
      "common.render": "Render",
      "header.connecting": "Connecting…",
      "header.settings": "Settings",
      "header.cameraActive": "Camera active",
      "header.cameraStopped": "Camera stopped",
      "header.connectionError": "Connection error",
      "restart.notice": "Saved changes become fully active after a restart.",
      "restart.action": "Restart BambuCam now",
      "restart.loading": "Restarting…",
      "restart.started": "BambuCam is restarting…",
      "restart.failed": "Restart failed: {message}",
      "camera.status": "Camera status",
      "camera.model": "Model",
      "camera.sensor": "Sensor",
      "camera.backend": "Backend",
      "camera.resolution": "Resolution",
      "camera.framerate": "Frame rate",
      "camera.restart": "Restart camera",
      "camera.actualFps": "{value} actual",
      "camera.maxFps": "max {value} fps",
      "system.title": "System",
      "system.cpuTemp": "CPU temp",
      "system.cpuLoad": "CPU load",
      "system.ramFree": "Free RAM",
      "system.diskFree": "Free disk",
      "system.ramTotal": "Total RAM",
      "system.diskTotal": "Total disk",
      "system.hostname": "Hostname",
      "system.piModel": "Pi model",
      "system.uptime": "Uptime",
      "update.title": "Software update",
      "update.installed": "Installed",
      "update.available": "Available",
      "update.release": "Release",
      "update.changelog": "Changelog →",
      "update.check": "Check for updates",
      "update.now": "Update now",
      "update.historyOpen": "Version history ▴",
      "update.historyClosed": "Version history ▾",
      "update.upToDate": "Up to date",
      "update.upToDateMessage": "✓ BambuCam is up to date",
      "update.availableVersion": "Update available: v{version}",
      "update.retry": "Try again",
      "update.toVersion": "Update to v{version}",
      "update.running": "Update in progress…",
      "update.checking": "Checking for updates…",
      "update.preparing": "Preparing update…",
      "update.downloading": "Downloading update…",
      "update.installing": "Installing update…",
      "update.restarting": "Restarting BambuCam…",
      "update.unknownError": "Unknown error",
      "update.confirm": "BambuCam will be updated and restarted automatically. Continue?",
      "update.confirmVersion": "v{version} will be installed. BambuCam will then restart. Continue?",
      "update.failed": "Update failed: {message}",
      "update.successReload": "Update successful! Reloading in {seconds}s…",
      "update.noReleases": "No versions found",
      "stream.noStream": "No stream",
      "stream.viewerOne": "viewer",
      "stream.viewerMany": "viewers",
      "stream.settings": "Stream settings",
      "stream.enableRtsp": "Enable RTSP",
      "stream.bitrate": "RTSP bitrate",
      "stream.applying": "Applying…",
      "stream.applied": "Stream settings applied",
      "stream.cameraRestarted": "Camera restarted",
      "stream.rtspStarted": "RTSP started",
      "stream.rtspStopped": "RTSP stopped",
      "image.settings": "Image settings",
      "image.brightness": "Brightness",
      "image.contrast": "Contrast",
      "image.saturation": "Saturation",
      "image.sharpness": "Sharpness",
      "image.exposureMode": "Exposure mode",
      "image.whiteBalance": "White balance",
      "image.noiseReduction": "Noise reduction",
      "image.verticalFlip": "Flip vertically",
      "image.horizontalFlip": "Flip horizontally",
      "image.autofocus": "Autofocus",
      "image.applied": "Image settings applied",
      "option.auto": "Auto",
      "option.sport": "Sport",
      "option.night": "Night",
      "option.sunlight": "Sunlight",
      "option.cloudy": "Cloudy",
      "option.shade": "Shade",
      "option.tungsten": "Tungsten",
      "option.fluorescent": "Fluorescent",
      "option.off": "Off",
      "option.minimal": "Minimal",
      "option.fast": "Fast",
      "option.highQuality": "High quality",
      "snapshot.title": "Snapshot",
      "snapshot.saveToDisk": "Save to disk",
      "snapshot.take": "Take snapshot",
      "snapshot.taking": "Capturing…",
      "snapshot.saved": "Snapshot saved",
      "snapshot.opened": "Snapshot opened",
      "snapshot.none": "No saved snapshots",
      "snapshot.download": "Download",
      "settings.title": "Settings",
      "settings.jpegQuality": "JPEG quality",
      "settings.targetFps": "Target fps",
      "settings.streamName": "Stream name",
      "settings.enableHls": "Enable HLS",
      "settings.webUi": "Web interface",
      "settings.passwordProtection": "Password protection",
      "settings.username": "Username",
      "settings.password": "Password",
      "settings.passwordUnchanged": "Leave unchanged",
      "settings.restartHint": "Port and authentication changes require a BambuCam restart.",
      "settings.saving": "Saving…",
      "settings.mjpegSaved": "MJPEG settings saved",
      "settings.rtspSaved": "RTSP settings saved",
      "settings.webSaved": "Web settings saved — restart required",
      "footer.description": "BambuCam — Open Source Raspberry Pi Camera Streaming",
      "footer.diagnostics": "Diagnostics package ↓",
      "profile.title": "Camera profile",
      "profile.field": "Profile",
      "profile.apply": "Apply profile",
      "profile.recommended": "Recommended",
      "profile.recommendedSuffix": "— recommended for this system",
      "profile.quality.label": "Maximum quality",
      "profile.quality.description": "Highest detected resolution with conservative frame rate and bitrate.",
      "profile.balanced.label": "Balanced",
      "profile.balanced.description": "1080p-oriented profile for monitoring and everyday use.",
      "profile.low_latency.label": "Low latency",
      "profile.low_latency.description": "720p-oriented profile with up to 30 FPS and short exposure.",
      "profile.low_power.label": "Low power",
      "profile.low_power.description": "Smallest detected mode, low frame rate, and reduced encoding load.",
      "timelapse.title": "Timelapse",
      "timelapse.ready": "Ready",
      "timelapse.jobTitle": "Title",
      "timelapse.jobPlaceholder": "Print job",
      "timelapse.interval": "Interval (s)",
      "timelapse.videoFps": "Video FPS",
      "timelapse.noSession": "No session yet",
      "timelapse.start": "Start",
      "timelapse.stopRender": "Stop & render",
      "timelapse.started": "Timelapse started",
      "timelapse.rendering": "Rendering…",
      "timelapse.finished": "Timelapse stopped and rendered",
      "timelapse.rendered": "Timelapse rendered",
      "timelapse.deleteConfirm": "Really delete this timelapse session?",
      "timelapse.deleted": "Timelapse deleted",
      "timelapse.noSessions": "No timelapse sessions yet.",
      "timelapse.render": "Render",
      "timelapse.delete": "Delete",
      "timelapse.frames": "{count} frames",
      "timelapse.stateRendering": "Rendering",
      "timelapse.stateRunning": "Running",
    },
    de: {
      "language.label": "Sprache",
      "common.apply": "Anwenden",
      "common.save": "Speichern",
      "common.loading": "Lade…",
      "common.wait": "Bitte warten…",
      "common.error": "Fehler: {message}",
      "common.close": "Schließen",
      "common.current": "Aktuell",
      "common.latest": "Neueste",
      "common.install": "Installieren",
      "common.delete": "Löschen",
      "common.render": "Rendern",
      "header.connecting": "Verbinde…",
      "header.settings": "Einstellungen",
      "header.cameraActive": "Kamera aktiv",
      "header.cameraStopped": "Kamera gestoppt",
      "header.connectionError": "Verbindungsfehler",
      "restart.notice": "Gespeicherte Änderungen werden nach einem Neustart vollständig aktiv.",
      "restart.action": "BambuCam jetzt neu starten",
      "restart.loading": "Neustart…",
      "restart.started": "BambuCam wird neu gestartet…",
      "restart.failed": "Neustart fehlgeschlagen: {message}",
      "camera.status": "Kamerastatus",
      "camera.model": "Modell",
      "camera.sensor": "Sensor",
      "camera.backend": "Backend",
      "camera.resolution": "Auflösung",
      "camera.framerate": "Framerate",
      "camera.restart": "Kamera neu starten",
      "camera.actualFps": "{value} real",
      "camera.maxFps": "max. {value} fps",
      "system.title": "System",
      "system.cpuTemp": "CPU-Temp.",
      "system.cpuLoad": "CPU-Last",
      "system.ramFree": "RAM frei",
      "system.diskFree": "Disk frei",
      "system.ramTotal": "RAM gesamt",
      "system.diskTotal": "Disk gesamt",
      "system.hostname": "Hostname",
      "system.piModel": "Pi-Modell",
      "system.uptime": "Uptime",
      "update.title": "Software-Update",
      "update.installed": "Installiert",
      "update.available": "Verfügbar",
      "update.release": "Release",
      "update.changelog": "Changelog →",
      "update.check": "Nach Updates suchen",
      "update.now": "Jetzt aktualisieren",
      "update.historyOpen": "Versionshistorie ▴",
      "update.historyClosed": "Versionshistorie ▾",
      "update.upToDate": "Aktuell",
      "update.upToDateMessage": "✓ BambuCam ist aktuell",
      "update.availableVersion": "Update verfügbar: v{version}",
      "update.retry": "Erneut versuchen",
      "update.toVersion": "Auf v{version} aktualisieren",
      "update.running": "Update läuft…",
      "update.checking": "Prüfe auf Updates…",
      "update.preparing": "Bereite Update vor…",
      "update.downloading": "Lade Update herunter…",
      "update.installing": "Installiere Update…",
      "update.restarting": "Starte BambuCam neu…",
      "update.unknownError": "Unbekannter Fehler",
      "update.confirm": "BambuCam wird aktualisiert und danach automatisch neu gestartet. Fortfahren?",
      "update.confirmVersion": "v{version} wird installiert. Danach startet BambuCam neu. Fortfahren?",
      "update.failed": "Fehler beim Update: {message}",
      "update.successReload": "Update erfolgreich! Seite wird in {seconds}s neu geladen…",
      "update.noReleases": "Keine Versionen gefunden",
      "stream.noStream": "Kein Stream",
      "stream.viewerOne": "Zuschauer",
      "stream.viewerMany": "Zuschauer",
      "stream.settings": "Streameinstellungen",
      "stream.enableRtsp": "RTSP aktivieren",
      "stream.bitrate": "RTSP-Bitrate",
      "stream.applying": "Wird angewendet…",
      "stream.applied": "Streameinstellungen übernommen",
      "stream.cameraRestarted": "Kamera wurde neu gestartet",
      "stream.rtspStarted": "RTSP gestartet",
      "stream.rtspStopped": "RTSP gestoppt",
      "image.settings": "Bildeinstellungen",
      "image.brightness": "Helligkeit",
      "image.contrast": "Kontrast",
      "image.saturation": "Sättigung",
      "image.sharpness": "Schärfe",
      "image.exposureMode": "Belichtungsmodus",
      "image.whiteBalance": "Weißabgleich",
      "image.noiseReduction": "Rauschunterdrückung",
      "image.verticalFlip": "Vertikal spiegeln",
      "image.horizontalFlip": "Horizontal spiegeln",
      "image.autofocus": "Autofokus",
      "image.applied": "Bildeinstellungen übernommen",
      "option.auto": "Auto",
      "option.sport": "Sport",
      "option.night": "Nacht",
      "option.sunlight": "Sonnenlicht",
      "option.cloudy": "Bewölkt",
      "option.shade": "Schatten",
      "option.tungsten": "Glühlampe",
      "option.fluorescent": "Leuchtstoffröhre",
      "option.off": "Aus",
      "option.minimal": "Minimal",
      "option.fast": "Schnell",
      "option.highQuality": "Hohe Qualität",
      "snapshot.title": "Snapshot",
      "snapshot.saveToDisk": "Auf Disk speichern",
      "snapshot.take": "Snapshot aufnehmen",
      "snapshot.taking": "Aufnehmen…",
      "snapshot.saved": "Snapshot gespeichert",
      "snapshot.opened": "Snapshot geöffnet",
      "snapshot.none": "Keine gespeicherten Snapshots",
      "snapshot.download": "Herunterladen",
      "settings.title": "Einstellungen",
      "settings.jpegQuality": "JPEG-Qualität",
      "settings.targetFps": "Ziel-fps",
      "settings.streamName": "Stream-Name",
      "settings.enableHls": "HLS aktivieren",
      "settings.webUi": "Web-Oberfläche",
      "settings.passwordProtection": "Passwortschutz",
      "settings.username": "Benutzername",
      "settings.password": "Passwort",
      "settings.passwordUnchanged": "Unverändert lassen",
      "settings.restartHint": "Port- und Auth-Änderungen erfordern einen Neustart von BambuCam.",
      "settings.saving": "Speichern…",
      "settings.mjpegSaved": "MJPEG-Einstellungen gespeichert",
      "settings.rtspSaved": "RTSP-Einstellungen gespeichert",
      "settings.webSaved": "Web-Einstellungen gespeichert — Neustart erforderlich",
      "footer.description": "BambuCam — Open Source Raspberry Pi Camera Streaming",
      "footer.diagnostics": "Diagnosepaket ↓",
      "profile.title": "Kamera-Profil",
      "profile.field": "Profil",
      "profile.apply": "Profil anwenden",
      "profile.recommended": "Empfohlen",
      "profile.recommendedSuffix": "— für dieses System empfohlen",
      "profile.quality.label": "Maximale Qualität",
      "profile.quality.description": "Höchste erkannte Auflösung mit konservativer Framerate und Bitrate.",
      "profile.balanced.label": "Ausgeglichen",
      "profile.balanced.description": "1080p-orientiertes Profil für Überwachung und den täglichen Einsatz.",
      "profile.low_latency.label": "Niedrige Latenz",
      "profile.low_latency.description": "720p-orientiertes Profil mit bis zu 30 FPS und kurzer Belichtung.",
      "profile.low_power.label": "Stromsparend",
      "profile.low_power.description": "Kleinster erkannter Modus, niedrige Framerate und reduzierte Encoderlast.",
      "timelapse.title": "Timelapse",
      "timelapse.ready": "Bereit",
      "timelapse.jobTitle": "Titel",
      "timelapse.jobPlaceholder": "Druckauftrag",
      "timelapse.interval": "Intervall (s)",
      "timelapse.videoFps": "Video-FPS",
      "timelapse.noSession": "Noch keine Sitzung",
      "timelapse.start": "Starten",
      "timelapse.stopRender": "Stoppen & rendern",
      "timelapse.started": "Timelapse gestartet",
      "timelapse.rendering": "Rendere…",
      "timelapse.finished": "Timelapse beendet und gerendert",
      "timelapse.rendered": "Timelapse gerendert",
      "timelapse.deleteConfirm": "Timelapse-Sitzung wirklich löschen?",
      "timelapse.deleted": "Timelapse gelöscht",
      "timelapse.noSessions": "Noch keine Timelapse-Sitzungen.",
      "timelapse.render": "Rendern",
      "timelapse.delete": "Löschen",
      "timelapse.frames": "{count} Frames",
      "timelapse.stateRendering": "Rendert",
      "timelapse.stateRunning": "Läuft",
    },
  };

  function normaliseLanguage(value) {
    const language = String(value || "").toLowerCase().split("-")[0];
    return SUPPORTED_LANGUAGES.includes(language) ? language : "en";
  }

  function storedLanguage() {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch (_) {
      return null;
    }
  }

  function persistLanguage(language) {
    try {
      localStorage.setItem(STORAGE_KEY, language);
    } catch (_) {
      // The UI still works when storage is disabled; only persistence is unavailable.
    }
  }

  function detectLanguage() {
    const saved = storedLanguage();
    if (SUPPORTED_LANGUAGES.includes(saved)) return saved;
    const browserLanguages = navigator.languages || [navigator.language || "en"];
    return browserLanguages.some((language) => normaliseLanguage(language) === "de") ? "de" : "en";
  }

  let currentLanguage = detectLanguage();

  function t(key, values = {}) {
    const table = translations[currentLanguage] || translations.en;
    let result = table[key] ?? translations.en[key] ?? key;
    Object.entries(values).forEach(([name, value]) => {
      result = result.split(`{${name}}`).join(String(value));
    });
    return result;
  }

  function translateDocument(root = document) {
    function matchingElements(selector) {
      const matches = Array.from(root.querySelectorAll(selector));
      if (root.matches && root.matches(selector)) matches.unshift(root);
      return matches;
    }

    matchingElements("[data-i18n]").forEach((element) => {
      element.textContent = t(element.dataset.i18n);
    });
    matchingElements("[data-i18n-placeholder]").forEach((element) => {
      element.placeholder = t(element.dataset.i18nPlaceholder);
    });
    matchingElements("[data-i18n-title]").forEach((element) => {
      element.title = t(element.dataset.i18nTitle);
    });
    matchingElements("[data-i18n-aria-label]").forEach((element) => {
      element.setAttribute("aria-label", t(element.dataset.i18nAriaLabel));
    });
    document.documentElement.lang = currentLanguage;
    const selector = document.getElementById("language-select");
    if (selector) selector.value = currentLanguage;
  }

  function setLanguage(language, persist = true) {
    const nextLanguage = normaliseLanguage(language);
    if (persist) persistLanguage(nextLanguage);
    const changed = nextLanguage !== currentLanguage;
    currentLanguage = nextLanguage;
    translateDocument();
    if (changed) {
      window.dispatchEvent(new CustomEvent("bambucam:languagechange", {
        detail: { language: currentLanguage },
      }));
    }
  }

  function initialise() {
    translateDocument();
    const selector = document.getElementById("language-select");
    if (selector) {
      selector.addEventListener("change", (event) => setLanguage(event.target.value));
    }
  }

  window.BambuCamI18n = {
    getLanguage: () => currentLanguage,
    setLanguage,
    supportedLanguages: [...SUPPORTED_LANGUAGES],
    t,
    translateDocument,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialise);
  } else {
    initialise();
  }
})();
