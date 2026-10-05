(() => {
  "use strict";

  function create(send) {
    const pending = [];
    let running = false;
    let settled = Promise.resolve();

    function enqueue(kind, values = {}) {
      // Replace only adjacent unsent motion. Never move a drag across button-up.
      const last = pending[pending.length - 1];
      if (kind === "move" && last?.kind === "move") last.values = values;
      else pending.push({ kind, values });
      if (!running) {
        running = true;
        settled = Promise.resolve().then(async () => {
          try {
            while (pending.length) {
              const next = pending.shift();
              try { await send(next.kind, next.values); } catch (_) {}
            }
          } finally { running = false; }
        });
      }
      return settled;
    }

    async function idle() {
      while (true) {
        const current = settled;
        await current;
        if (current === settled && !running && pending.length === 0) return;
      }
    }

    return Object.freeze({ enqueue, idle });
  }

  globalThis.TailDeskInputQueue = Object.freeze({ create });
})();
