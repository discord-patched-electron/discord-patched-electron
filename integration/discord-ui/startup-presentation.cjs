"use strict";
// Warm the shared page-world presentation cache without opening DPE Settings.
// Read loaded Discord assets only; never import arbitrary modules or account data.
module.exports = function startupPresentation(_request, ui, collect) {
  const key = Symbol.for("dpe.startupPresentation");
  if (globalThis[key] || typeof document === "undefined") return;
  const state = { stopped: false, timer: null, attempts: 0 };
  globalThis[key] = state;
  const refresh = () => {
    if (state.stopped) return;
    try {
      collect({ iconsOnly: true }, ui);
    } catch {
      // Discord's styles/modules may still be loading. Keep safe local fallbacks.
    }
    if (++state.attempts < 7 && typeof setTimeout === "function")
      state.timer = setTimeout(refresh, 5100);
  };
  const stop = () => {
    state.stopped = true;
    clearTimeout(state.timer);
  };
  globalThis.addEventListener?.("pagehide", stop, { once: true });
  refresh();
};
