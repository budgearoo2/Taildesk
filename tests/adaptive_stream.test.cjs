const assert = require("node:assert/strict");
const test = require("node:test");

require("../taildesk/web/adaptive_stream.js");

test("ramps toward but never exceeds the configured frame-rate cap", () => {
  let state = { fps: 12, quality: 65, qualityCap: 65, fastFrames: 0 };
  for (let index = 0; index < 200; index++) {
    state = globalThis.TailDeskAdaptiveStream.adjust({ ...state, qualityCap: 65, targetFps: 60, elapsedMs: 1 });
  }
  assert.equal(state.fps, 60);
  assert.equal(state.quality, 65);
});

test("reduces frame rate and JPEG quality after an overloaded frame", () => {
  const next = globalThis.TailDeskAdaptiveStream.adjust({
    targetFps: 60, fps: 60, quality: 65, qualityCap: 65, fastFrames: 3, elapsedMs: 25,
  });
  assert.deepEqual(next, { fps: 45, quality: 60, fastFrames: 0 });
});

test("respects minimum image quality and recovers only after sustained fast frames", () => {
  let state = { fps: 1, quality: 25, fastFrames: 0 };
  const adjust = (current, elapsedMs) => globalThis.TailDeskAdaptiveStream.adjust({
    ...current, targetFps: 1, qualityCap: 65, elapsedMs,
  });
  state = adjust(state, 1200);
  assert.equal(state.quality, 25);
  assert.equal(state.fps, 1);
  for (let index = 0; index < 4; index++) {
    state = adjust(state, 1);
  }
  assert.deepEqual(state, { fps: 1, quality: 27, fastFrames: 0 });
});
