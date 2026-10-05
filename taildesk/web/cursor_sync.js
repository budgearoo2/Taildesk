(() => {
  "use strict";
  const allowed = new Set([
    "default", "text", "wait", "crosshair", "nwse-resize", "nesw-resize",
    "ew-resize", "ns-resize", "move", "not-allowed", "pointer", "progress", "help", "none",
  ]);

  function create(element) {
    let lastStamp = -1;
    function update(headers) {
      const name = headers.get("X-TailDesk-Cursor");
      const value = headers.get("X-TailDesk-Cursor-Stamp");
      if (!allowed.has(name) || value === null) return;
      const stamp = Number(value);
      if (!Number.isFinite(stamp) || stamp < 0 || stamp < lastStamp) return;
      lastStamp = stamp;
      if (element.style.cursor !== name) element.style.cursor = name;
    }
    function reset() {
      lastStamp = -1;
      element.style.cursor = "default";
    }
    return Object.freeze({ update, reset });
  }
  globalThis.TailDeskCursorSync = Object.freeze({ create });
})();
