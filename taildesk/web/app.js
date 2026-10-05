(() => {
  const $ = (id) => document.getElementById(id);
  const desktop = $("desktop");
  const clipboardText = $("clipboard-text");
  let active = false;
  let frameTimer;
  let heartbeatTimer;
  let clipboardTimer;
  let inputQueue = Promise.resolve();
  let held = new Set();
  let fps = 8;
  let lastHostClipboard = null;
  let toastTimer;

  async function api(path, options = {}) {
    const response = await fetch(path, { cache: "no-store", ...options });
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
    active = false;
    clearTimeout(frameTimer);
    clearInterval(heartbeatTimer);
    clearInterval(clipboardTimer);
    $("login").classList.remove("hidden");
    $("connection").textContent = "Disconnected";
    $("connection").classList.remove("online");
  }

  async function connect() {
    try {
      await api("/api/state");
      active = true;
      $("login").classList.add("hidden");
      $("connection").textContent = "Connected";
      $("connection").classList.add("online");
      await heartbeat();
      refreshFrame();
      heartbeatTimer = setInterval(() => heartbeat().catch(() => {}), 2500);
      clipboardTimer = setInterval(() => readHostClipboard(false), 900);
    } catch (_) {
      showLogin();
    }
  }

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

  function heartbeat() {
    const size = targetSize();
    return api("/api/heartbeat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(size),
    });
  }

  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => active && heartbeat().catch(() => {}), 300);
  });

  function refreshFrame() {
    if (!active) return;
    desktop.onload = () => {
      frameTimer = setTimeout(refreshFrame, Math.max(50, 1000 / fps));
    };
    desktop.onerror = () => {
      frameTimer = setTimeout(refreshFrame, 1000);
    };
    desktop.src = `/api/screen?t=${Date.now()}`;
  }

  function input(kind, values = {}) {
    // Preserve key down/up ordering. Mouse click requests also carry coordinates,
    // so a click cannot race a separate pointer-move request.
    inputQueue = inputQueue
      .then(() =>
        api("/api/input", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ kind, ...values }),
        }),
      )
      .catch(() => {});
    return inputQueue;
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

  let latestMove = null;
  let movePending = false;
  desktop.addEventListener("pointermove", (event) => {
    latestMove = screenPoint(event);
    if (!movePending) {
      movePending = true;
      setTimeout(() => {
        movePending = false;
        if (latestMove) input("move", latestMove);
      }, 40);
    }
  });
  desktop.addEventListener("pointerdown", (event) => {
    desktop.focus();
    const button = event.button === 2 ? "right" : event.button === 1 ? "middle" : "left";
    input("click", { ...screenPoint(event), button });
    event.preventDefault();
  });
  desktop.addEventListener("contextmenu", (event) => event.preventDefault());
  desktop.addEventListener("wheel", (event) => {
    input("scroll", { amount: event.deltaY < 0 ? 3 : -3 });
    event.preventDefault();
  }, { passive: false });

  const aliases = {
    Control: "ctrl", Shift: "shift", Alt: "alt", Meta: "win", " ": "space",
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
    if (event.ctrlKey && event.key.toLowerCase() === "v") {
      pasteToHost(true);
      return;
    }
    const key = keyName(event);
    if (key && !held.has(key)) {
      held.add(key);
      input("down", { key });
    }
    if (event.ctrlKey && event.key.toLowerCase() === "c") {
      setTimeout(() => readHostClipboard(true), 500);
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
  }

  async function readHostClipboard(showFallback) {
    if (!active) return;
    try {
      const data = await api("/api/clipboard");
      if (!data.text) return;
      if (data.text === lastHostClipboard) return;
      lastHostClipboard = data.text;
      if (window.isSecureContext && navigator.clipboard?.writeText) {
        try {
          await navigator.clipboard.writeText(data.text);
        } catch (_) {
          if (showFallback) {
            clipboardText.value = data.text;
            $("clipboard-fallback").classList.remove("hidden");
            toast("Browser clipboard permission was denied. Copy the host text from the clipboard panel.");
          }
        }
      } else if (showFallback) {
        clipboardText.value = data.text;
        $("clipboard-fallback").classList.remove("hidden");
        toast("Host clipboard loaded. Use “Copy to laptop clipboard”.");
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
    } catch (error) {
      toast(error.message);
    }
  }
  $("clipboard").addEventListener("click", pasteToHost);
  $("copy-fallback").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(clipboardText.value);
      toast("Copied to this device.");
    } catch (_) {
      clipboardText.select();
      document.execCommand("copy");
      toast("Copied to this device.");
    }
  });
  $("send-fallback").addEventListener("click", async () => {
    try {
      await api("/api/clipboard", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: clipboardText.value }),
      });
      toast("Copied to the host clipboard.");
    } catch (error) {
      toast(error.message);
    }
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
      fps = data.fps;
      $("bind-host").value = data.bind_host;
      $("port").value = data.port;
      $("fps").value = data.fps;
      $("quality").value = data.jpeg_quality;
      $("transfer-folder").value = data.transfer_folder;
      $("clipboard-enabled").checked = data.clipboard_enabled;
      $("tailscale-https").checked = data.tailscale_https;
      $("startup").checked = data.startup;
      const state = await api("/api/state");
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
      $("settings").classList.add("hidden");
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
  });

  connect();
})();
