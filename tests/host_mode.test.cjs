const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

async function loadViewer({ host = true, authenticated = true } = {}) {
  const requests = [];
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
    window, document: { getElementById: nodes, addEventListener() {}, createElement: () => ({}) },
    navigator: { platform: "Win32" }, performance: { now: () => 0 },
    setTimeout: () => 1, clearTimeout() {},
    setInterval: (fn) => { intervalCallbacks.push(fn); return intervalCallbacks.length; }, clearInterval() {},
    fetch: async (url, options = {}) => {
      requests.push(url);
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
  for (const filename of ["stream_stats.js", "input_queue.js", "adaptive_stream.js", "screen_protocol.js", "cursor_sync.js", "realtime.js", "app.js"]) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, "../taildesk/web", filename), "utf8"), context, { filename });
  }
  await new Promise(setImmediate);
  return { requests, nodes, intervalCallbacks };
}

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
