(() => {
  const $ = (id) => document.getElementById(id);
  const desktop = $("desktop");
  const screenContext = desktop.getContext("2d", { alpha: false });
  const cursorSync = window.TailDeskCursorSync.create(desktop);
  const clipboardText = $("clipboard-text");
  const streamStats = window.TailDeskStreamStats.create();
  const appleKeyboard = /Mac|iPhone|iPad/.test(navigator.platform || "");
  let active = false;
  let frameTimer;
  let heartbeatTimer;
  let clipboardTimer;
  let hostStatusTimer;
  let hostMode = false;
  let selectedMonitor = "";
  let switchingMonitor = false;
  let frameGeneration = 0;
  let currentFrame = Promise.resolve();
  const inputQueue = window.TailDeskInputQueue.create((kind, values) =>
    realtime.input(kind, { monitor: selectedMonitor, ...values }) ? Promise.resolve() : api("/api/input", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, monitor: selectedMonitor, ...values }),
    }),
  );
  let held = new Set();
  let heldButtons = new Set();
  let fps = 8;
  let adaptiveFps = 6;
  let configuredQuality = 65;
  let adaptiveQuality = 65;
  let fastFrames = 0;
  let forceFullFrame = true;
  let audioContext = null;
  let audioPlaying = false;
  let audioAvailable = false;
  let audioPollTimer;
  let audioInstallPollTimer;
  let audioNextTime = 0;
  let lastHostClipboard = null;
  let toastTimer;
  let latestStats = null;
  let realtimeEnabled = false;
  const mediaVideo = $("realtime-video");
  const realtime = window.TailDeskRealtime.create({
    video: mediaVideo, canvas: desktop, api, cursor: cursorSync,
    onFallback: () => {
      api("/api/realtime/stop", { method: "POST" }).catch(() => {});
      if (active) {
        forceFullFrame = true;
        clearTimeout(frameTimer);
        refreshFrame();
        if (audioPlaying) pollAudio();
        toast("Using compatibility stream; realtime connection was interrupted.");
      }
    },
    onStats: metrics => {
      $("stats-bitrate").textContent = window.TailDeskStreamStats.formatBitrate(metrics.bitrate);
      $("stats-fps").textContent = `${metrics.fps.toFixed(1)} decoded fps`;
      $("stats-latency").textContent = `${metrics.rtt.toFixed(1)} ms network RTT / ${metrics.decodeMs.toFixed(1)} ms decode`;
      $("stats-resolution").textContent = `${metrics.width || desktop.width} x ${metrics.height || desktop.height}`;
      const viewport = targetSize();
      $("stats-viewport").textContent = `${viewport.width} x ${viewport.height}`;
      $("stats-stream").textContent = `Realtime H.264 + Opus / ${fps} fps cap`;
    },
  });
  async function startMedia() {
    const generation = frameGeneration;
    if (realtimeEnabled && active) {
      const connected = await realtime.start();
      if (generation !== frameGeneration) return;
      if (!active) { realtime.stop(); return; }
      if (connected) return;
      await api("/api/realtime/stop", { method: "POST" }).catch(() => {});
      toast("Realtime unavailable; using compatibility stream.");
    }
    if (active) refreshFrame();
  }

  function renderStats(metrics = latestStats) {
    if (!metrics || realtime.active) return;
    const viewport = targetSize();
    $("stats-bitrate").textContent = window.TailDeskStreamStats.formatBitrate(metrics.receiveBitsPerSecond);
    $("stats-fps").textContent = `${metrics.updatedFps.toFixed(1)} fps (${metrics.pollFps.toFixed(1)} polls/s)`;
    $("stats-latency").textContent = metrics.averageFrameMs ? `${metrics.averageFrameMs.toFixed(0)} ms` : "—";
    $("stats-resolution").textContent = desktop.width && desktop.height
      ? `${desktop.width} × ${desktop.height}` : "Waiting for a frame";
    $("stats-viewport").textContent = `${viewport.width} × ${viewport.height}`;
    $("stats-stream").textContent = `${adaptiveFps.toFixed(1)} fps adaptive / ${fps} fps cap · JPEG ${adaptiveQuality}%`;
  }

  $("stats-toggle").addEventListener("click", () => {
    const overlay = $("stats-overlay");
    const open = overlay.classList.contains("hidden");
    overlay.classList.toggle("hidden", !open);
    $("stats-toggle").setAttribute("aria-pressed", String(open));
    if (open) renderStats();
  });

  async function api(path, options = {}) {
    const response = await fetch(path, { cache: "no-store", ...options });
    if (response.ok && active) cursorSync.update(response.headers);
    const type = response.headers.get("content-type") || "";
    const data = type.includes("application/json") ? await response.json() : null;
    if (response.status === 401 && path !== "/api/login") showLogin();
    if (!response.ok) throw new Error(data?.error || `Request failed (${response.status})`);
    return data;
  }

  function toast(message) {
    const node = $("toast");
    node.textContent = message;
    node.style.display = "block";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (node.style.display = "none"), 3000);
  }

  function showLogin() {
    realtime.stop();
    mediaVideo.muted = true;
    frameGeneration += 1;
    $("monitor-select").disabled = true;
    cursorSync.reset();
    active = false;
    clearTimeout(frameTimer);
    clearInterval(heartbeatTimer);
    clearInterval(clipboardTimer);
    clearInterval(hostStatusTimer);
    audioPlaying = false;
    clearTimeout(audioPollTimer);
    audioContext?.suspend();
    $("audio-toggle").textContent = audioAvailable ? "Enable remote sound" : "Install VB-CABLE";
    $("login").classList.remove("hidden");
    $("connection").textContent = "Disconnected";
    $("connection").classList.remove("online");
  }

  function renderHostStatus(state) {
    $("remote-url").value = state.remote_url || "Remote access is not ready";
    $("copy-url").disabled = !state.remote_url;
    $("host-status").textContent = state.connected ? "Your other device is connected." : "Waiting for your other device.";
    $("host-error").textContent = state.restart_required
      ? "Settings saved. Quit TailDesk from the tray and reopen it to apply the connection changes."
      : state.listener_error || "";
    $("host-https").textContent = state.https_url
      ? `Optional HTTPS address for your other device: ${state.https_url}`
      : state.https_error ? `Optional HTTPS setup: ${state.https_error}` : "";
    updateAudioStatus(state.audio);
  }

  $("copy-url").addEventListener("click", async () => {
    const field = $("remote-url");
    try {
      await navigator.clipboard.writeText(field.value);
      toast("Address copied. Open it on your other device.");
    } catch (_) {
      field.select();
      toast("Press Ctrl+C to copy the selected address.");
    }
  });

  async function connect() {
    try {
      const state = await api("/api/state");
      if (state.version) $("version").textContent = `v${state.version}`;
      fps = state.settings.fps;
      realtimeEnabled = !!state.realtime;
      configuredQuality = state.settings.jpeg_quality;
      if (state.host_browser) {
        hostMode = true;
        active = false;
        clearTimeout(frameTimer);
        clearInterval(heartbeatTimer);
        clearInterval(clipboardTimer);
        clearInterval(hostStatusTimer);
        $("login").classList.add("hidden");
        $("desktop").classList.add("hidden");
        $("host-home").classList.remove("hidden");
        $("connection").textContent = "Host settings";
        $("sign-out").classList.toggle("hidden", !!state.local_access);
        for (const id of ["fullscreen", "monitor-select", "audio-toggle", "stats-toggle", "clipboard", "files-toggle"]) $(id).classList.add("hidden");
        renderHostStatus(state);
        hostStatusTimer = setInterval(async () => {
          try { renderHostStatus(await api("/api/state")); }
          catch (_) { $("host-error").textContent = "TailDesk is unavailable. Reopen the app from its installed folder."; }
        }, 5000);
        return;
      }
      active = true;
      frameGeneration += 1;
      cursorSync.reset();
      adaptiveFps = Math.min(fps, 30);
      adaptiveQuality = configuredQuality;
      streamStats.reset();
      latestStats = streamStats.snapshot();
      forceFullFrame = true;
      $("login").classList.add("hidden");
      $("sign-out").classList.toggle("hidden", !!state.local_access);
      $("connection").textContent = "Connected";
      $("connection").classList.add("online");
      await heartbeat();
      const connectedState = await api("/api/state");
      updateAudioStatus(connectedState.audio);
      heartbeatTimer = setInterval(() => heartbeat().catch(() => {}), 2500);
      startMedia();
      clipboardTimer = setInterval(readHostClipboard, 900);
    } catch (_) {
      showLogin();
    }
  }

  function updateAudioStatus(status) {
    $("audio-status").textContent = status?.message || "Remote audio is unavailable.";
    audioAvailable = !!status?.available;
    $("audio-toggle").disabled = !!status?.installing;
    if (!audioPlaying) {
      $("audio-toggle").textContent = audioAvailable
        ? "Enable remote sound"
        : (status?.installing ? "VB-CABLE installer open…" : "Install VB-CABLE");
    }
  }

  function pollAudioDriverInstall() {
    clearInterval(audioInstallPollTimer);
    audioInstallPollTimer = setInterval(async () => {
      try {
        const state = await api("/api/state");
        updateAudioStatus(state.audio);
        if (state.audio?.available || !state.audio?.installing) {
          clearInterval(audioInstallPollTimer);
          if (state.audio?.available) toast("VB-CABLE is ready. Enable remote sound to listen.");
        }
      } catch (_) {
        clearInterval(audioInstallPollTimer);
      }
    }, 2500);
  }

  async function pollAudio() {
    if (!active || !audioPlaying || !audioContext || realtime.active) return;
    let nextDelay = 0;
    try {
      const response = await fetch(`/api/audio?t=${Date.now()}`, { cache: "no-store" });
      if (response.status === 204) nextDelay = 5;
      else if (!response.ok) nextDelay = 250;
      else {
        const bytes = new DataView(await response.arrayBuffer());
        const frames = bytes.byteLength / 4;
        if (!Number.isInteger(frames) || frames < 1) throw new Error("Invalid audio frame");
        const buffer = audioContext.createBuffer(2, frames, 48000);
        const left = buffer.getChannelData(0);
        const right = buffer.getChannelData(1);
        for (let index = 0; index < frames; index++) {
          left[index] = bytes.getInt16(index * 4, true) / 32768;
          right[index] = bytes.getInt16(index * 4 + 2, true) / 32768;
        }
        const now = audioContext.currentTime;
        if (audioNextTime < now || audioNextTime > now + 0.1) audioNextTime = now + 0.02;
        const source = audioContext.createBufferSource();
        source.buffer = buffer;
        source.connect(audioContext.destination);
        source.start(audioNextTime);
        audioNextTime += buffer.duration;
        nextDelay = 0;
      }
    } catch (_) {
      nextDelay = 200;
    }
    if (audioPlaying) audioPollTimer = setTimeout(pollAudio, nextDelay);
  }

  $("audio-toggle").addEventListener("click", async () => {
    if (!audioAvailable) {
      $("audio-toggle").disabled = true;
      try {
        const result = await api("/api/audio/install", { method: "POST" });
        updateAudioStatus(result.audio);
        toast(result.message);
        if (result.audio?.installing) pollAudioDriverInstall();
      } catch (error) {
        toast(error.message || "Could not open the VB-CABLE installer.");
        const state = await api("/api/state").catch(() => null);
        updateAudioStatus(state?.audio);
      }
      return;
    }
    if (audioPlaying) {
      mediaVideo.muted = true;
      audioPlaying = false;
      clearTimeout(audioPollTimer);
      await audioContext?.suspend();
      $("audio-toggle").textContent = "Enable remote sound";
      return;
    }
    try {
      const Context = window.AudioContext || window.webkitAudioContext;
      if (Context) {
        audioContext ||= new Context({ latencyHint: "interactive", sampleRate: 48000 });
        await audioContext.resume();
      }
      mediaVideo.muted = false;
      if (realtime.active) {
        await mediaVideo.play();
        audioPlaying = true;
        $("audio-toggle").textContent = "Mute remote sound";
        return;
      }
      if (!Context) throw new Error("This browser does not support remote audio playback.");
      audioContext ||= new Context({ latencyHint: "interactive", sampleRate: 48000 });
      await audioContext.resume();
      audioNextTime = audioContext.currentTime + 0.04;
      audioPlaying = true;
      $("audio-toggle").textContent = "Mute remote sound";
      pollAudio();
    } catch (error) {
      toast(error.message || "Allow audio playback in your browser, then try again.");
    }
  });

  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("login-error").textContent = "";
    try {
      await api("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: $("password").value }),
      });
      $("password").value = "";
      await connect();
    } catch (error) {
      $("login-error").textContent = error.message;
    }
  });

  function targetSize() {
    const ratio = window.devicePixelRatio || 1;
    return {
      width: Math.round(window.innerWidth * ratio),
      height: Math.round(Math.max(360, (window.innerHeight - 48) * ratio)),
    };
  }

  function renderMonitors(state) {
    const monitors = state.monitors || [];
    const selector = $("monitor-select");
    const next = state.selected_monitor || monitors[0]?.id || "";
    if (next !== selectedMonitor) {
      const wasSelected = !!selectedMonitor;
      selectedMonitor = next;
      forceFullFrame = true;
      cursorSync.reset();
      if (wasSelected && active && !switchingMonitor) {
        frameGeneration += 1;
        clearTimeout(frameTimer);
        currentFrame.finally(() => { if (active && !switchingMonitor && !realtime.active) refreshFrame(); });
      }
    }
    const signature = JSON.stringify(monitors);
    if (selector.dataset.monitors !== signature) {
      selector.replaceChildren(...monitors.map((monitor) => {
        const option = document.createElement("option");
        option.value = monitor.id;
        option.textContent = `${monitor.label} · ${monitor.width} × ${monitor.height}`;
        return option;
      }));
      selector.dataset.monitors = signature;
    }
    selector.value = selectedMonitor;
    selector.disabled = !active || switchingMonitor || monitors.length < 2;
  }

  $("monitor-select").addEventListener("change", async () => {
    if (!active || switchingMonitor) return;
    const requested = $("monitor-select").value;
    realtime.stop();
    switchingMonitor = true;
    $("monitor-select").disabled = true;
    releaseKeys();
    active = false;
    frameGeneration += 1;
    clearTimeout(frameTimer);
    clearInterval(heartbeatTimer);
    try {
      await inputQueue.idle();
      await currentFrame;
      const state = await api("/api/monitor", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ monitor: requested }),
      });
      renderMonitors(state);
    } catch (error) {
      toast(error.message);
      $("monitor-select").value = selectedMonitor;
    } finally {
      switchingMonitor = false;
      active = $("login").classList.contains("hidden");
      forceFullFrame = true;
      cursorSync.reset();
      if (active) {
        await heartbeat().catch((error) => toast(error.message));
        startMedia();
        heartbeatTimer = setInterval(() => heartbeat().catch(() => {}), 2500);
      }
    }
  });

  async function heartbeat() {
    const size = targetSize();
    const generation = frameGeneration;
    const state = await api("/api/heartbeat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(size),
    });
    if (generation === frameGeneration && !switchingMonitor) renderMonitors(state);
    return state;
  }

  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => active && heartbeat().catch(() => {}), 300);
  });

  function refreshFrame() {
    if (!active || realtime.active) return;
    const generation = frameGeneration;
    const monitorAtStart = selectedMonitor;
    const isCurrent = () => active && generation === frameGeneration && monitorAtStart === selectedMonitor;
    const started = performance.now();
    let responseBytes = 0;
    let updatedFrame = false;
    const url = `/api/screen?t=${Date.now()}&q=${adaptiveQuality}&monitor=${encodeURIComponent(selectedMonitor)}${forceFullFrame ? "&full=1" : ""}`;
    forceFullFrame = false;
    currentFrame = fetch(url, { cache: "no-store" }).then(async (response) => {
      if (!isCurrent()) return;
      if (response.status === 401) { showLogin(); return; }
      if (response.status === 409) throw new Error("Desktop session ended");
      if (response.ok && active) cursorSync.update(response.headers);
      if (response.status === 204) return;
      if (!response.ok) throw new Error(`Screen request failed (${response.status})`);
      const monitor = response.headers.get("X-TailDesk-Monitor");
      if (monitor && monitor !== selectedMonitor) { forceFullFrame = true; return; }
      const type = response.headers.get("content-type") || "";
      if (type.includes("application/vnd.taildesk.tiles")) {
        const bytes = await response.arrayBuffer();
        responseBytes = bytes.byteLength;
        const update = window.TailDeskScreenProtocol.decodeDeltaFrame(bytes);
        if (desktop.width !== update.width || desktop.height !== update.height) {
          forceFullFrame = true;
          return;
        }
        await window.TailDeskScreenProtocol.drawDeltaFrame(screenContext, update, createImageBitmap, isCurrent);
        updatedFrame = update.tiles.length > 0;
      } else {
        const bytes = await response.arrayBuffer();
        responseBytes = bytes.byteLength;
        const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/jpeg" }));
        if (!isCurrent()) { bitmap.close(); return; }
        if (desktop.width !== bitmap.width || desktop.height !== bitmap.height) {
          desktop.width = bitmap.width;
          desktop.height = bitmap.height;
        }
        screenContext.drawImage(bitmap, 0, 0);
        bitmap.close();
        updatedFrame = true;
      }
    }).catch((error) => {
      forceFullFrame = true;
      if (error.message === "Desktop session ended") toast(error.message);
    }).finally(() => {
      if (!isCurrent()) return;
      const elapsed = performance.now() - started;
      latestStats = streamStats.record({ byteLength: responseBytes, updated: updatedFrame, elapsedMs: elapsed });
      if (!$("stats-overlay").classList.contains("hidden")) renderStats(latestStats);
      const adjusted = window.TailDeskAdaptiveStream.adjust({
        targetFps: fps,
        fps: adaptiveFps,
        quality: adaptiveQuality,
        qualityCap: configuredQuality,
        fastFrames,
        elapsedMs: elapsed,
      });
      adaptiveFps = adjusted.fps;
      adaptiveQuality = adjusted.quality;
      fastFrames = adjusted.fastFrames;
      frameTimer = setTimeout(refreshFrame, Math.max(0, 1000 / adaptiveFps - elapsed));
    });
  }

  function input(kind, values = {}) {
    if (hostMode || !active) return Promise.resolve();
    // Coalesce pointer movement while preserving ordered key/button transitions.
    return inputQueue.enqueue(kind, values);
  }

  function screenPoint(event) {
    const box = desktop.getBoundingClientRect();
    const x = box.width ? (event.clientX - box.left) / box.width : 0;
    const y = box.height ? (event.clientY - box.top) / box.height : 0;
    return {
      x: Math.max(0, Math.min(1, x)),
      y: Math.max(0, Math.min(1, y)),
    };
  }

  desktop.addEventListener("pointermove", (event) => {
    if (active) input("move", screenPoint(event));
  });
  desktop.addEventListener("pointerdown", (event) => {
    desktop.focus();
    desktop.setPointerCapture?.(event.pointerId);
    const button = event.button === 2 ? "right" : event.button === 1 ? "middle" : "left";
    heldButtons.add(button);
    input("mouse_down", { ...screenPoint(event), button });
    event.preventDefault();
  });
  function releaseButton(event) {
    const button = event.button === 2 ? "right" : event.button === 1 ? "middle" : "left";
    if (!heldButtons.has(button)) return;
    heldButtons.delete(button);
    input("mouse_up", { ...screenPoint(event), button });
  }
  desktop.addEventListener("pointerup", releaseButton);
  desktop.addEventListener("pointercancel", (event) => {
    const point = screenPoint(event);
    for (const button of heldButtons) input("mouse_up", { ...point, button });
    heldButtons.clear();
  });
  desktop.addEventListener("contextmenu", (event) => event.preventDefault());
  desktop.addEventListener("wheel", (event) => {
    const factor = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? 16
      : event.deltaMode === WheelEvent.DOM_DELTA_PAGE ? window.innerHeight : 1;
    input("scroll", { delta: event.deltaY * factor });
    event.preventDefault();
  }, { passive: false });

  const aliases = {
    Control: "ctrl", Shift: "shift", Alt: "alt", Meta: appleKeyboard ? "ctrl" : "win", " ": "space",
    ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right",
    Escape: "esc", Backspace: "backspace", Delete: "delete", Enter: "enter",
    Tab: "tab", CapsLock: "capslock", PageUp: "pageup", PageDown: "pagedown",
    Home: "home", End: "end", Insert: "insert",
  };
  function keyName(event) {
    if (/^F\d{1,2}$/.test(event.key)) return event.key.toLowerCase();
    if (aliases[event.key]) return aliases[event.key];
    if (event.key.length === 1) return event.key.toLowerCase();
    return null;
  }
  desktop.addEventListener("keydown", (event) => {
    if (["F5", "F11"].includes(event.key)) return;
    event.preventDefault();
    const shortcut = event.ctrlKey || (appleKeyboard && event.metaKey);
    if (shortcut && event.key.toLowerCase() === "v") {
      pasteToHost(true);
      return;
    }
    const key = keyName(event);
    if (key && !held.has(key)) {
      held.add(key);
      input("down", { key });
    } else if (key && event.repeat) {
      input("down", { key, repeat: true });
    }
    if (shortcut && event.key.toLowerCase() === "c") {
      setTimeout(readHostClipboard, 500);
    }
  });
  desktop.addEventListener("keyup", (event) => {
    const key = keyName(event);
    if (key && held.has(key)) {
      held.delete(key);
      input("up", { key });
    }
  });
  window.addEventListener("blur", releaseKeys);
  function releaseKeys() {
    for (const key of held) input("up", { key });
    held.clear();
    for (const button of heldButtons) input("mouse_up", { ...(latestMove || { x: 0, y: 0 }), button });
    heldButtons.clear();
  }

  async function readHostClipboard() {
    if (!active) return;
    try {
      const data = await api("/api/clipboard");
      if (!data.text) return;
      if (data.text === lastHostClipboard) return;
      lastHostClipboard = data.text;
      clipboardText.value = data.text;
      if (window.isSecureContext && navigator.clipboard?.writeText) {
        try {
          await navigator.clipboard.writeText(data.text);
        } catch (_) {
          // Manual copy remains available from the Clipboard button.
        }
      }
    } catch (_) {}
  }

  async function pasteToHost(performHostPaste = false) {
    try {
      let text;
      if (window.isSecureContext && navigator.clipboard?.readText) {
        try { text = await navigator.clipboard.readText(); }
        catch (_) { text = window.prompt("Paste text to send to the host PC:", ""); }
      } else {
        text = window.prompt("Paste text to send to the host PC:", "");
      }
      if (text === null) return;
      await api("/api/clipboard", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (performHostPaste && held.has("ctrl")) {
        await input("down", { key: "v" });
        await input("up", { key: "v" });
      }
      toast("Copied to the host clipboard.");
      closeClipboardPanel();
    } catch (error) {
      toast(error.message);
    }
  }
  function closeClipboardPanel() {
    $("clipboard-fallback").classList.add("hidden");
    $("clipboard").setAttribute("aria-expanded", "false");
  }
  $("clipboard").addEventListener("click", async () => {
    const panel = $("clipboard-fallback");
    const open = panel.classList.contains("hidden");
    panel.classList.toggle("hidden", !open);
    $("clipboard").setAttribute("aria-expanded", String(open));
    if (open) await readHostClipboard();
  });
  $("copy-fallback").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(clipboardText.value);
      toast("Copied to this device.");
    } catch (_) {
      clipboardText.select();
      document.execCommand("copy");
      toast("Copied to this device.");
    }
    closeClipboardPanel();
  });
  $("send-fallback").addEventListener("click", async () => {
    try {
      await api("/api/clipboard", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: clipboardText.value }),
      });
      toast("Copied to the host clipboard.");
      closeClipboardPanel();
    } catch (error) {
      toast(error.message);
    }
  });
  $("clipboard-dismiss").addEventListener("click", () => {
    closeClipboardPanel();
  });

  $("fullscreen").addEventListener("click", () => document.documentElement.requestFullscreen?.());
  $("files-toggle").addEventListener("click", () => {
    $("files").classList.toggle("hidden");
    loadFiles();
  });
  $("files-close").addEventListener("click", () => $("files").classList.add("hidden"));
  function escapeHtml(value) {
    return value.replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[character]);
  }
  async function loadFiles() {
    if (!active) return;
    try {
      const data = await api("/api/files");
      $("file-list").innerHTML = data.files.length
        ? data.files.map((file) => `<div class="file-row"><span>${escapeHtml(file.name)} · ${Math.ceil(file.size / 1024)} KB</span><a href="/api/download/${encodeURIComponent(file.name)}">Download</a></div>`).join("")
        : '<p class="hint">No files in the host transfer folder yet.</p>';
    } catch (error) { toast(error.message); }
  }
  $("upload").addEventListener("change", async (event) => {
    for (const file of event.target.files) {
      try {
        const response = await fetch("/api/upload", {
          method: "POST",
          headers: { "X-File-Name": encodeURIComponent(file.name), "Content-Type": "application/octet-stream" },
          body: file,
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Upload failed");
        toast(`Uploaded ${data.name}`);
      } catch (error) { toast(error.message); }
    }
    event.target.value = "";
    loadFiles();
  });

  $("settings-toggle").addEventListener("click", async () => {
    try {
      const data = await api("/api/settings");
      const state = await api("/api/state");
      updateAudioStatus(state.audio);
      fps = data.fps;
      configuredQuality = data.jpeg_quality;
      $("bind-host").value = data.bind_host;
      $("port").value = data.port;
      $("fps").value = data.fps;
      $("quality").value = data.jpeg_quality;
      $("transfer-folder").value = data.transfer_folder;
      $("clipboard-enabled").checked = data.clipboard_enabled;
      $("tailscale-https").checked = data.tailscale_https;
      $("startup").checked = data.startup;
      $("secure-url").textContent = state.https_url
        ? `Private HTTPS address: ${state.https_url}`
        : state.https_error || "Private HTTPS address is not available; direct Tailnet IP still works with manual clipboard controls.";
      $("settings").classList.remove("hidden");
    } catch (error) { toast(error.message); }
  });
  $("settings-close").addEventListener("click", () => $("settings").classList.add("hidden"));
  $("settings-save").addEventListener("click", async () => {
    try {
      const data = await api("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bind_host: $("bind-host").value,
          port: Number($("port").value),
          fps: Number($("fps").value),
          jpeg_quality: Number($("quality").value),
          transfer_folder: $("transfer-folder").value,
          clipboard_enabled: $("clipboard-enabled").checked,
          tailscale_https: $("tailscale-https").checked,
          startup: $("startup").checked,
        }),
      });
      fps = data.settings.fps;
      configuredQuality = data.settings.jpeg_quality;
      if (active && realtimeEnabled) { frameGeneration += 1; realtime.stop(); clearTimeout(frameTimer); startMedia(); }
      adaptiveQuality = Math.min(adaptiveQuality, configuredQuality);
      $("settings").classList.add("hidden");
      if (hostMode) renderHostStatus(await api("/api/state"));
      if (data.restart_required) toast(data.https_url ? `Saved. Restart TailDesk to apply the port; secure URL: ${data.https_url}` : "Saved. Restart TailDesk to apply the bind address or port.");
      else if (data.https_url) toast(`Saved. Secure Tailnet URL: ${data.https_url}`);
      else if (data.https_error) toast(`Settings saved; HTTPS setup needs attention: ${data.https_error}`);
      else toast(data.restart_required ? "Saved. Restart TailDesk to apply the bind address or port." : "Settings saved.");
    } catch (error) { toast(error.message); }
  });

  $("sign-out").addEventListener("click", async () => {
    releaseKeys();
    try { await api("/api/logout", { method: "POST" }); } catch (_) {}
    showLogin();
  });
  window.addEventListener("pagehide", () => {
    if (active) navigator.sendBeacon("/api/disconnect", "");
    releaseKeys();
    realtime.stop();
  });

  connect();
})();
