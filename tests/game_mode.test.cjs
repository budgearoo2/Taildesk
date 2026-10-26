const assert = require("node:assert/strict");
const test = require("node:test");

require("../taildesk/web/game_mode.js");
const { hostCode, createExitCombo } = globalThis.TailDeskGameMode;

test("physical key codes pass through, and Command acts as Ctrl only on Apple keyboards", () => {
  assert.equal(hostCode("KeyF", false), "KeyF");
  assert.equal(hostCode("ShiftLeft", true), "ShiftLeft");
  assert.equal(hostCode("MetaLeft", true), "ControlLeft");
  assert.equal(hostCode("MetaRight", true), "ControlRight");
  assert.equal(hostCode("MetaLeft", false), "MetaLeft");
  assert.equal(hostCode("", false), null);
  assert.equal(hostCode("Unidentified", false), null);
});

test("Shift+E+Enter completes in any order with either Shift or Enter key", () => {
  for (const order of [
    ["ShiftLeft", "KeyE", "Enter"],
    ["Enter", "KeyE", "ShiftRight"],
    ["KeyE", "ShiftLeft", "NumpadEnter"],
  ]) {
    const combo = createExitCombo();
    assert.deepEqual(order.map((code) => combo.down(code)), [false, false, true]);
  }
});

test("partial combos, released keys and resets never complete", () => {
  const combo = createExitCombo();
  assert.equal(combo.down("ShiftLeft"), false);
  assert.equal(combo.down("KeyE"), false);
  combo.up("ShiftLeft");
  assert.equal(combo.down("Enter"), false);
  assert.deepEqual(combo.held().sort(), ["Enter", "KeyE"]);
  combo.reset();
  assert.equal(combo.down("ShiftLeft"), false);
  assert.deepEqual(combo.held(), ["ShiftLeft"]);
});
