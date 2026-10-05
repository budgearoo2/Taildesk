(() => {
  "use strict";

  function create(send) {
    let chain = Promise.resolve();
    let newestMove = null;
    let moveQueued = false;

    function enqueue(kind, values = {}) {
      if (kind === "move") {
        newestMove = values;
        if (moveQueued) return chain;
        moveQueued = true;
        chain = chain.then(async () => {
          const move = newestMove;
          newestMove = null;
          if (move) await send("move", move);
        }).catch(() => {}).finally(() => {
          moveQueued = false;
          if (newestMove) enqueue("move", newestMove);
        });
        return chain;
      }

      // Keep key, button, and click transitions ordered with pointer movement.
      chain = chain.then(() => send(kind, values)).catch(() => {});
      return chain;
    }

    async function idle() {
      while (true) {
        const pending = chain;
        await pending;
        if (pending === chain && !moveQueued && newestMove === null) return;
      }
    }

    return Object.freeze({ enqueue, idle });
  }

  globalThis.TailDeskInputQueue = Object.freeze({ create });
})();
