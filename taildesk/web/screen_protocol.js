(() => {
  "use strict";

  function decodeDeltaFrame(buffer) {
    const view = new DataView(buffer);
    if (buffer.byteLength < 14 || view.getUint8(0) !== 84 || view.getUint8(1) !== 68
      || view.getUint8(2) !== 76 || view.getUint8(3) !== 49) {
      throw new Error("Invalid screen update");
    }
    const width = view.getUint32(4);
    const height = view.getUint32(8);
    const tileCount = view.getUint16(12);
    if (!width || !height || width > 65535 || height > 65535) {
      throw new Error("Invalid screen dimensions");
    }
    const tiles = [];
    let offset = 14;
    for (let index = 0; index < tileCount; index++) {
      if (offset + 12 > buffer.byteLength) throw new Error("Truncated screen update");
      const x = view.getUint16(offset);
      const y = view.getUint16(offset + 2);
      const tileWidth = view.getUint16(offset + 4);
      const tileHeight = view.getUint16(offset + 6);
      const jpegLength = view.getUint32(offset + 8);
      offset += 12;
      if (!tileWidth || !tileHeight || x + tileWidth > width || y + tileHeight > height
        || !jpegLength || offset + jpegLength > buffer.byteLength) {
        throw new Error("Invalid screen tile");
      }
      tiles.push({
        x,
        y,
        width: tileWidth,
        height: tileHeight,
        jpeg: new Blob([buffer.slice(offset, offset + jpegLength)], { type: "image/jpeg" }),
      });
      offset += jpegLength;
    }
    if (offset !== buffer.byteLength) throw new Error("Invalid screen update length");
    return { width, height, tiles };
  }

  globalThis.TailDeskScreenProtocol = Object.freeze({ decodeDeltaFrame });
})();
