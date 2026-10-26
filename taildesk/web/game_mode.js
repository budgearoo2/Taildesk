(() => {
  "use strict";

  const EXIT_LABEL = "Shift+E+Enter";

  // Game mode sends KeyboardEvent.code, the physical key, so the host can inject
  // the hardware scan code that games read instead of the typed character.
  function hostCode(code, appleKeyboard) {
    if (!code || code === "Unidentified") return null;
    // Command acts as Ctrl, as in the normal viewer, so a Mac never sends the
    // Windows key (which would open Start over the game).
    if (appleKeyboard && code === "MetaLeft") return "ControlLeft";
    if (appleKeyboard && code === "MetaRight") return "ControlRight";
    return code;
  }

  // Shift, E and Enter held together, pressed in any order, leave game mode.
  function createExitCombo() {
    const held = new Set();
    const complete = () => (held.has("ShiftLeft") || held.has("ShiftRight"))
      && held.has("KeyE") && (held.has("Enter") || held.has("NumpadEnter"));
    return Object.freeze({
      down(code) { held.add(code); return complete(); },
      up(code) { held.delete(code); },
      held: () => [...held],
      reset() { held.clear(); },
    });
  }

  globalThis.TailDeskGameMode = Object.freeze({ EXIT_LABEL, hostCode, createExitCombo });
})();
