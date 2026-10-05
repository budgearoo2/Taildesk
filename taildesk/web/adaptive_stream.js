(() => {
  "use strict";

  function adjust({ targetFps, fps, quality, qualityCap, fastFrames, elapsedMs }) {
    const budget = 1000 / Math.max(1, fps);
    if (elapsedMs > budget * 1.15) {
      return {
        fps: Math.max(1, Math.floor(fps * 0.75)),
        quality: Math.max(25, quality - 5),
        fastFrames: 0,
      };
    }
    if (elapsedMs < budget * 0.55) {
      fastFrames += 1;
      if (fastFrames >= 4) {
        return {
          fps: Math.min(targetFps, Math.max(fps + 1, Math.ceil(fps * 1.18))),
          quality: Math.min(qualityCap, quality + 2),
          fastFrames: 0,
        };
      }
      return { fps, quality, fastFrames };
    }
    return { fps, quality, fastFrames: 0 };
  }

  globalThis.TailDeskAdaptiveStream = Object.freeze({ adjust });
})();
