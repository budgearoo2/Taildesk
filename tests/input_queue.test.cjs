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
    ["move", { x: 3 }],
    ["mouse_down", { x: 3, button: "left" }],
    ["mouse_up", { x: 3, button: "left" }],
  ]);
});

test("motion stays on the correct side of drag and key transitions even after a failed send", async () => {
  const calls = [];
  const queue = globalThis.TailDeskInputQueue.create(async (kind, values) => {
    calls.push([kind, values]);
    if (kind === "move" && values.x === 2) throw new Error("network failure");
  });
  queue.enqueue("move", { x: 1 });
  queue.enqueue("move", { x: 2 });
  queue.enqueue("mouse_down", { button: "left", x: 2 });
  queue.enqueue("move", { x: 3 });
  queue.enqueue("move", { x: 4 });
  queue.enqueue("mouse_up", { button: "left", x: 4 });
  queue.enqueue("down", { key: "a" });
  queue.enqueue("up", { key: "a" });
  await queue.idle();
  assert.deepEqual(calls.map(([kind, values]) => [kind, values.x ?? values.key]), [
    ["move", 2], ["mouse_down", 2], ["move", 4], ["mouse_up", 4], ["down", "a"], ["up", "a"],
  ]);
});
