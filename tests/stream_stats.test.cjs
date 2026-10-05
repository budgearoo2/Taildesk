const assert = require("node:assert/strict");
const test = require("node:test");

require("../taildesk/web/stream_stats.js");

test("reports updated-frame FPS, polling FPS, image bitrate, and frame time", () => {
  let now = 0;
  const stats = globalThis.TailDeskStreamStats.create(() => now);

  stats.record({ byteLength: 1000, updated: true, elapsedMs: 40 });
  now = 500;
  stats.record({ byteLength: 1000, updated: true, elapsedMs: 60 });
  now = 1000;
  const sample = stats.snapshot();

  assert.equal(sample.updatedFps, 2);
  assert.equal(sample.pollFps, 2);
  assert.equal(sample.receiveBitsPerSecond, 16000);
  assert.equal(sample.averageFrameMs, 50);
});

test("formats receive bitrate in readable units", () => {
  const format = globalThis.TailDeskStreamStats.formatBitrate;
  assert.equal(format(900), "900 b/s");
  assert.equal(format(12500), "12.5 kb/s");
  assert.equal(format(2500000), "2.50 Mb/s");
});
