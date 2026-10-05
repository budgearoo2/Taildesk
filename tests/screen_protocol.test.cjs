const assert = require("node:assert/strict");
const { Buffer } = require("node:buffer");
const test = require("node:test");

require("../taildesk/web/screen_protocol.js");

function frame({ width = 640, height = 480, x = 10, y = 20, tileWidth = 2, tileHeight = 2, jpeg = [0xff, 0xd8, 0xff, 0xd9] } = {}) {
  const bytes = Buffer.alloc(14 + 12 + jpeg.length);
  bytes.write("TDL1", 0, "ascii");
  bytes.writeUInt32BE(width, 4);
  bytes.writeUInt32BE(height, 8);
  bytes.writeUInt16BE(1, 12);
  bytes.writeUInt16BE(x, 14);
  bytes.writeUInt16BE(y, 16);
  bytes.writeUInt16BE(tileWidth, 18);
  bytes.writeUInt16BE(tileHeight, 20);
  bytes.writeUInt32BE(jpeg.length, 22);
  Buffer.from(jpeg).copy(bytes, 26);
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
}

function toArrayBuffer(bytes) {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
}

test("decodes binary delta-frame header, bounds, and JPEG payload", () => {
  const decoded = globalThis.TailDeskScreenProtocol.decodeDeltaFrame(frame());
  assert.deepEqual([decoded.width, decoded.height, decoded.tiles.length], [640, 480, 1]);
  const tile = decoded.tiles[0];
  assert.deepEqual([tile.x, tile.y, tile.width, tile.height], [10, 20, 2, 2]);
  assert.equal(tile.jpeg.type, "image/jpeg");
  assert.equal(tile.jpeg.size, 4);
});

test("rejects truncated and out-of-bounds delta frames", () => {
  const valid = Buffer.from(frame());
  assert.throws(() => globalThis.TailDeskScreenProtocol.decodeDeltaFrame(toArrayBuffer(valid.subarray(0, 20))), /Truncated/);
  assert.throws(() => globalThis.TailDeskScreenProtocol.decodeDeltaFrame(frame({ x: 639 })), /Invalid screen tile/);
});

test("rejects bad signatures and trailing payload bytes", () => {
  const badSignature = Buffer.from(frame());
  badSignature.write("NOPE", 0, "ascii");
  assert.throws(() => globalThis.TailDeskScreenProtocol.decodeDeltaFrame(toArrayBuffer(badSignature)), /Invalid screen update/);
  const extra = Buffer.concat([Buffer.from(frame()), Buffer.from([0])]);
  assert.throws(() => globalThis.TailDeskScreenProtocol.decodeDeltaFrame(toArrayBuffer(extra)), /Invalid screen update length/);
});
