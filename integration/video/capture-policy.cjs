"use strict";
// Change only the choice of picker for the next user-initiated share. Discord
// owns presentation, source selection, cancellation and the native source ID.
module.exports = function capturePolicy(request, ui) {
  if (request?.platform !== "linux" || typeof ui?.watch !== "function") return;
  const key = Symbol.for("dpe.capturePolicy");
  let state = globalThis[key];
  if (state) {
    state.request = request;
    globalThis[Symbol.for("dpe.capturePickerUI")]?.configure?.(request);
    return;
  }
  state = { request, installed: 0 };
  globalThis[key] = state;
  const wrapped = new WeakSet();
  ui.watch(
    "dpe-capture-policy",
    (source) =>
      [
        "MediaEngineStore",
        "getUseSystemScreensharePicker",
        "supportsSystemScreensharePicker",
      ].every((part) => source.includes(part)),
    (exports) => {
      const candidates = ui
        .inspect({ c: { picker: { exports } } })
        .objects.filter(
          (value) =>
            value?.constructor?.displayName === "MediaEngineStore" &&
            typeof value.getUseSystemScreensharePicker === "function" &&
            typeof value.supportsSystemScreensharePicker === "function" &&
            typeof value.getMediaEngine === "function",
        );
      if (candidates.length !== 1 || wrapped.has(candidates[0])) return;
      const store = candidates[0];
      // The trusted main process uses this read-only capability for X11 KMS.
      // It does not declare native picker support or initialize a media engine.
      state.supported = () => {
        try {
          return store.supportsSystemScreensharePicker() === true;
        } catch {
          return false;
        }
      };
      const original = store.getUseSystemScreensharePicker;
      store.getUseSystemScreensharePicker = function (...args) {
        const automatic = Reflect.apply(original, this, args);
        const preference = state.request;
        // Experimental sources use Discord's existing native-picker lifecycle.
        // Keep its real capability check; the Vanilla tab still uses that same
        // unmodified system picker, including the normal sync/latency fixes.
        if (preference.experimentalCapture && this.supportsSystemScreensharePicker()) return true;
        if (preference.method === "kms" && this.supportsSystemScreensharePicker()) return true;
        if (
          ["portal", "wayland-direct", "vkcapture"].includes(preference.method) &&
          preference.wayland &&
          this.supportsSystemScreensharePicker()
        )
          return true;
        if (preference.method === "x11" && preference.x11 && !preference.wayland) return false;
        return automatic;
      };
      wrapped.add(store);
      state.installed++;
    },
  );
};
