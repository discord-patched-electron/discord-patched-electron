"use strict";
// Runs in the page world; no Node or privileged bridge. Release a closing
// browsing-context name synchronously, before Chromium can reuse it.
module.exports = function popoutLifecycle() {
  const installed = Symbol.for("dpe.popoutLifecycle");
  if (globalThis[installed]) return;
  globalThis[installed] = true;
  const video = (name) =>
    name === "DISCORD_CHANNEL_CALL_POPOUT" ||
    (typeof name === "string" && name.startsWith("DISCORD_CALL_TILE_POPOUT_"));
  const wrapped = new WeakSet();
  function protect(target) {
    try {
      const close = target.close;
      if (wrapped.has(close)) return;
      const replacement = function (...args) {
        const name = target.name;
        if (video(name)) target.name = "";
        try {
          return Reflect.apply(close, this, args);
        } catch (error) {
          target.name = name;
          throw error;
        }
      };
      wrapped.add(replacement);
      target.close = replacement;
    } catch {
      /* Cross-origin or protected globals retain browser behavior. */
    }
  }
  if (video(globalThis.name)) protect(globalThis);
  const open = globalThis.open;
  globalThis.open = function (...args) {
    const [url, name] = args;
    const child = Reflect.apply(open, this, args);
    if (child && video(name)) {
      try {
        const address = new URL(url, location.href);
        if (address.origin === location.origin && address.pathname === "/popout") protect(child);
      } catch {
        /* Native open already handled invalid addresses. */
      }
    }
    return child;
  };
};
