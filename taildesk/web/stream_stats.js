(() => {
  "use strict";

  function create(now = () => performance.now()) {
    let startedAt = now();
    let requests = 0;
    let frames = 0;
    let bytes = 0;
    let latencyTotal = 0;

    function reset(at = now()) {
      startedAt = at;
      requests = 0;
      frames = 0;
      bytes = 0;
      latencyTotal = 0;
    }

    function snapshot(at = now()) {
      const elapsedMs = Math.max(1, at - startedAt);
      const result = {
        pollFps: requests * 1000 / elapsedMs,
        updatedFps: frames * 1000 / elapsedMs,
        receiveBitsPerSecond: bytes * 8 * 1000 / elapsedMs,
        averageFrameMs: requests ? latencyTotal / requests : 0,
      };
      if (elapsedMs >= 1000) reset(at);
      return result;
    }

    function record({ byteLength = 0, updated = false, elapsedMs = 0 } = {}) {
      requests += 1;
      bytes += Math.max(0, Number(byteLength) || 0);
      if (updated) frames += 1;
      latencyTotal += Math.max(0, Number(elapsedMs) || 0);
      return snapshot();
    }

    return Object.freeze({ record, reset, snapshot });
  }

  function formatBitrate(bitsPerSecond) {
    const value = Math.max(0, Number(bitsPerSecond) || 0);
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)} Mb/s`;
    if (value >= 1_000) return `${(value / 1_000).toFixed(1)} kb/s`;
    return `${Math.round(value)} b/s`;
  }

  globalThis.TailDeskStreamStats = Object.freeze({ create, formatBitrate });
})();
