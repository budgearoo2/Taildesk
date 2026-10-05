const assert = require("node:assert/strict");
const test = require("node:test");

require("../taildesk/web/input_queue.js");

test("coalesces stale pointer moves but preserves button transition order", async () => {
  const calls = [];
  let releaseFirstMove;
  const queue = globalThis.TailDeskInputQueue.create((kind, values) => {
    calls.push([kind, values]);
    if (kind === "move" && values.x === 1) {
      return new Promise((resolve) => { releaseFirstMove = resolve; });
    }
    return Promise.resolve();
  });

  queue.enqueue("move", { x: 1 });
  await Promise.resolve();
  await Promise.resolve();
  queue.enqueue("move", { x: 2 });
  queue.enqueue("move", { x: 3 });
  queue.enqueue("mouse_down", { x: 3, button: "left" });
  queue.enqueue("mouse_up", { x: 3, button: "left" });
  releaseFirstMove();
  await queue.idle();

  assert.deepEqual(calls, [
    ["move", { x: 1 }],
    ["mouse_down", { x: 3, button: "left" }],
    ["mouse_up", { x: 3, button: "left" }],
    ["move", { x: 3 }],
  ]);
});
