const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

async function loadViewer({ host = true, authenticated = true } = {}) {
  const requests = [];
  const inputs = [];
  const elements = new Map();
  const intervalCallbacks = [];
  const nodes = (id) => {
    if (!elements.has(id)) {
      const classes = new Set(["hidden"]);
      const events = {};
      elements.set(id, {
        value: "", textContent: "", style: {}, dataset: {}, events, width: 1280, height: 720,
        classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c), toggle(c, enabled) { if (enabled) classes.add(c); else classes.delete(c); } },
        addEventListener: (event, callback) => { events[event] = callback; },
        pause() {}, getContext: () => ({}), setAttribute() {}, focus() {}, select() {},
        replaceChildren(...items) { this.children = items; },
      });
    }
    return elements.get(id);
  };
  const state = {
    host_browser: host, local_access: host, version: "test", connected: false,
    remote_url: "http://192.0.2.20:8765/", settings: { fps: 60, jpeg_quality: 65 }, audio: { available: false },
    monitors: [{ id: "display-1", label: "Screen 1", width: 1920, height: 1080 }, { id: "display-2", label: "Screen 2", width: 1920, height: 1080 }], selected_monitor: "display-1",
  };
  const window = { addEventListener() {}, innerWidth: 1280, innerHeight: 768, devicePixelRatio: 1 };
  const context = vm.createContext({
    window, document: { getElementById: nodes, addEventListener() {}, createElement: () => ({}), documentElement: {} },
    navigator: { platform: "Win32" }, performance: { now: () => 0 },
    setTimeout: () => 1, clearTimeout() {},
    setInterval: (fn) => { intervalCallbacks.push(fn); return intervalCallbacks.length; }, clearInterval() {},
    fetch: async (url, options = {}) => {
      requests.push(url);
      if (url === "/api/input") inputs.push(JSON.parse(options.body));
      if (url === "/api/login") authenticated = true;
      if (url === "/api/monitor") state.selected_monitor = JSON.parse(options.body).monitor;
      return {
        status: authenticated ? (url.startsWith("/api/screen") ? 204 : 200) : 401,
        ok: authenticated,
        headers: { get: (key) => key === "content-type" ? "application/json" : null },
        json: async () => authenticated ? state : { error: "Sign in" },
      };
    },
    console,
  });
  Object.assign(context, window);
  context.window = context;
  for (const filename of ["stream_stats.js", "input_queue.js", "adaptive_stream.js", "screen_protocol.js", "cursor_sync.js", "realtime.js", "game_mode.js", "app.js"]) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, "../taildesk/web", filename), "utf8"), context, { filename });
  }
  await new Promise(setImmediate);
  return { requests, inputs, nodes, intervalCallbacks };
}

const keyEvent = (code, key, extra = {}) => ({ code, key, repeat: false, ctrlKey: false, metaKey: false, preventDefault() {}, ...extra });
async function settle() {
  for (let index = 0; index < 6; index++) await new Promise(setImmediate);
}
const sentKeys = (inputs) => inputs.map(({ kind, key, code, repeat }) => [kind, key ?? code, ...(repeat ? ["repeat"] : [])]);

test("host startup and status refresh never request screen, input, or a control heartbeat", async () => {
  const { requests, nodes, intervalCallbacks } = await loadViewer();
  assert.equal(nodes("connection").textContent, "Host settings");
  assert.equal(nodes("remote-url").value, "http://192.0.2.20:8765/");
  assert.equal(nodes("desktop").classList.contains("hidden"), true);
  assert.equal(nodes("host-home").classList.contains("hidden"), false);
  for (const callback of intervalCallbacks) await callback();
  assert.ok(requests.length >= 2);
  assert.ok(requests.every((url) => url === "/api/state"));
});

test("new remote browser reaches login and can start viewing after authentication", async () => {
  const { requests, nodes } = await loadViewer({ host: false, authenticated: false });
  assert.equal(nodes("login").classList.contains("hidden"), false);
  assert.deepEqual(requests, ["/api/state"]);
  nodes("password").value = "test password";
  await nodes("login-form").events.submit({ preventDefault() {} });
  await new Promise(setImmediate);
  assert.equal(nodes("connection").textContent, "Connected");
  assert.ok(requests.includes("/api/heartbeat"));
  assert.ok(requests.some((url) => url.startsWith("/api/screen")));
  assert.equal(nodes("login").classList.contains("hidden"), true);
});

test("masthead screen selector switches the stream and resumes its heartbeat", async () => {
  const { requests, nodes } = await loadViewer({ host: false });
  assert.equal(nodes("monitor-select").children.length, 2);
  assert.equal(nodes("monitor-select").disabled, false);
  assert.equal(nodes("monitor-select").value, "display-1");
  nodes("monitor-select").value = "display-2";
  await nodes("monitor-select").events.change();
  await new Promise(setImmediate);
  assert.equal(nodes("monitor-select").value, "display-2");
  assert.ok(requests.includes("/api/monitor"));
  assert.ok(requests.some((url) => url.includes("monitor=display-2") && url.includes("full=1")));
  assert.equal(nodes("monitor-select").disabled, false);
});

test("outside game mode the viewer still sends typed key names", async () => {
  const { nodes, inputs } = await loadViewer({ host: false });
  nodes("desktop").events.keydown(keyEvent("KeyF", "f"));
  nodes("desktop").events.keyup(keyEvent("KeyF", "f"));
  await settle();
  assert.deepEqual(sentKeys(inputs), [["down", "f"], ["up", "f"]]);
});

test("game mode sends physical keys, holds and repeats them, and forwards browser keys like F5", async () => {
  const { nodes, inputs } = await loadViewer({ host: false });
  nodes("game-mode").events.click();
  assert.equal(nodes("game-mode").classList.contains("active"), true);
  const desktop = nodes("desktop").events;
  desktop.keydown(keyEvent("KeyF", "f"));
  desktop.keydown(keyEvent("KeyF", "f", { repeat: true }));
  desktop.keyup(keyEvent("KeyF", "f"));
  desktop.keydown(keyEvent("F5", "F5"));
  desktop.keyup(keyEvent("F5", "F5"));
  await settle();
  assert.deepEqual(sentKeys(inputs), [
    ["game_down", "KeyF"], ["game_down", "KeyF", "repeat"], ["game_up", "KeyF"], ["game_down", "F5"], ["game_up", "F5"],
  ]);
});

test("Shift+E+Enter leaves game mode without sending Enter or leaking the held keys", async () => {
  const { nodes, inputs } = await loadViewer({ host: false });
  nodes("game-mode").events.click();
  const desktop = nodes("desktop").events;
  desktop.keydown(keyEvent("ShiftLeft", "Shift"));
  desktop.keydown(keyEvent("KeyE", "E"));
  desktop.keydown(keyEvent("Enter", "Enter"));
  desktop.keydown(keyEvent("Enter", "Enter", { repeat: true }));
  for (const [code, key] of [["Enter", "Enter"], ["KeyE", "E"], ["ShiftLeft", "Shift"]]) desktop.keyup(keyEvent(code, key));
  desktop.keydown(keyEvent("KeyF", "f"));
  await settle();
  assert.equal(nodes("game-mode").classList.contains("active"), false);
  assert.deepEqual(sentKeys(inputs), [
    ["game_down", "ShiftLeft"], ["game_down", "KeyE"], ["game_up", "ShiftLeft"], ["game_up", "KeyE"], ["down", "f"],
  ]);
});

test("the IN GAME button turns game mode off again and releases held keys", async () => {
  const { nodes, inputs } = await loadViewer({ host: false });
  nodes("game-mode").events.click();
  nodes("desktop").events.keydown(keyEvent("KeyW", "w"));
  nodes("game-mode").events.click();
  nodes("desktop").events.keyup(keyEvent("KeyW", "w"));
  await settle();
  assert.equal(nodes("game-mode").classList.contains("active"), false);
  assert.deepEqual(sentKeys(inputs), [["game_down", "KeyW"], ["game_up", "KeyW"]]);
});
