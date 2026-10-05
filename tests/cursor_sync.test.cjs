const assert = require("node:assert/strict");
const test = require("node:test");
require("../taildesk/web/cursor_sync.js");
const headers = (cursor, stamp) => new Map([["X-TailDesk-Cursor", cursor], ["X-TailDesk-Cursor-Stamp", String(stamp)]]);

test("updates local cursor shape and rejects older responses arriving out of order", () => {
  const canvas = { style: {} };
  const sync = globalThis.TailDeskCursorSync.create(canvas);
  sync.update(headers("text", 12));
  assert.equal(canvas.style.cursor, "text");
  sync.update(headers("default", 11));
  assert.equal(canvas.style.cursor, "text");
  sync.update(headers("ew-resize", 13));
  assert.equal(canvas.style.cursor, "ew-resize");
  sync.update(headers("none", 14));
  assert.equal(canvas.style.cursor, "none");
  sync.reset();
  assert.equal(canvas.style.cursor, "default");
  sync.update(headers("pointer", 1));
  assert.equal(canvas.style.cursor, "pointer");
});

test("ignores missing headers, malformed timestamps, and arbitrary CSS", () => {
  const canvas = { style: { cursor: "text" } };
  const sync = globalThis.TailDeskCursorSync.create(canvas);
  sync.update(new Map());
  sync.update(headers("pointer", "NaN"));
  sync.update(headers("pointer", -1));
  sync.update(headers("url(https://example.invalid/cursor), auto", 1));
  assert.equal(canvas.style.cursor, "text");
});
