"use strict";
const path = require("node:path");
let adapter,
  ipc,
  installed = false,
  available = [],
  methodReasons = {},
  fps = 60;
let probing = false,
  lastProbe = 0;
const selections = new Map();
const listeners = new Set();
function publish(event) {
  for (const listener of listeners) {
    try {
      listener(event);
    } catch (error) {
      console.error("DPE capture lifecycle:", error.message);
    }
  }
}
function notifyPicker(value) {
  if (typeof window !== "undefined")
    window.dispatchEvent(
      new CustomEvent("dpe-capture-picker-result", { detail: JSON.stringify(value) }),
    );
}
function cancelSelection(id) {
  const selection = selections.get(id);
  if (!selection || selection.active) return;
  // The bridge clears only this request's matching native configuration and
  // closes only an FD that has not transferred to a capture constructor.
  // Calling the global portal release here could stop a newer system picker.
  if (adapter.cancel(id) === false) return;
  selections.delete(id);
  ipc.send("dpe-capture-event", { event: "cancelled", requestId: id });
}
function refreshMethods() {
  if (!adapter || probing || Date.now() - lastProbe < (available.length ? 30000 : 2000)) return;
  probing = true;
  lastProbe = Date.now();
  void ipc
    .invoke("dpe-capture-methods")
    .then((value) => {
      const methods = Array.isArray(value) ? value : value?.methods;
      available = ["wayland-direct", "vkcapture", "kms"].filter(
        (method) => Array.isArray(methods) && methods.includes(method),
      );
      methodReasons = {};
      const reason = value?.methodReasons?.kms;
      if (
        !available.includes("kms") &&
        [
          "helper-missing",
          "permission-required",
          "permission-blocked",
          "authorization-unavailable",
          "no-outputs",
          "unsupported",
          "native-picker-unavailable",
        ].includes(reason)
      )
        methodReasons.kms = reason;
    })
    .catch(() => {})
    .finally(() => {
      probing = false;
    });
}
function install(directory, voice) {
  if (installed || process.platform !== "linux") return;
  installed = true;
  try {
    ipc = require("electron").ipcRenderer;
    const settings = require("./dpe-screenshare-settings.cjs");
    if (typeof window !== "undefined") {
      let requests = 0;
      window.addEventListener("dpe-capture-picker-request", async (event) => {
        if (typeof event.detail !== "string" || event.detail.length > 4096 || requests >= 8) return;
        let request;
        try {
          request = JSON.parse(event.detail);
        } catch {
          return;
        }
        if (
          typeof request?.id !== "string" ||
          !/^[a-f0-9-]{36}$/.test(request.id) ||
          ![
            "list",
            "arm",
            "disarm",
            "preview",
            "preview-stop",
            "picker-close",
            "open-settings",
          ].includes(request.op)
        )
          return;
        requests++;
        let response;
        try {
          if (
            !adapter ||
            (!["disarm", "preview-stop", "picker-close", "open-settings"].includes(request.op) &&
              !settings.read().experimentalCapture)
          )
            throw new Error(
              "Experimental capture is unavailable. Use Vanilla or check DPE Settings.",
            );
          response = {
            value: await ipc.invoke("dpe-capture-picker", {
              op: request.op,
              token: request.token,
              width: request.width,
              height: request.height,
              handoff: request.handoff === true,
            }),
          };
        } catch (error) {
          response = { error: String(error.message).slice(0, 512) };
        } finally {
          requests--;
        }
        window.dispatchEvent(
          new CustomEvent("dpe-capture-picker-reply", {
            detail: JSON.stringify({ id: request.id, ...response }),
          }),
        );
      });
    }
    adapter = require("./dpe-capture-picker.cjs").load(
      directory,
      path.join(process.env.DPE_APP_ROOT, "libdpe-camera-probe.so"),
      async (event) => {
        const id = event.requestId;
        if (event.event === "request") {
          for (const previous of selections.keys()) cancelSelection(previous);
          if (!settings.read().experimentalCapture) {
            adapter.finish(id, "native");
            return;
          }
          selections.set(id, { pending: true });
          try {
            const result = await ipc.invoke("dpe-capture-inline-request", { ...event, fps });
            if (!selections.has(id)) {
              ipc.send("dpe-capture-event", { ...event, event: "cancelled" });
              return;
            }
            selections.set(id, {
              requestId: id,
              method: result.method,
              sourceType: result.sourceType,
              handoff: result.handoff,
              handoffFallback: result.handoffFallback,
              active: false,
            });
            const accepted = adapter.finish(id, result.action, result.socketPath, result.nodeId);
            notifyPicker({
              action: accepted === false ? "cancel" : result.action,
              error: typeof result.error === "string" ? result.error.slice(0, 512) : undefined,
            });
            if (accepted === false || result.action !== "selected") {
              selections.delete(id);
              ipc.send("dpe-capture-event", { ...event, event: "cancelled" });
            }
          } catch (error) {
            adapter.finish(id, "error");
            notifyPicker({ action: "error", error: String(error.message).slice(0, 512) });
            selections.delete(id);
            ipc.send("dpe-capture-event", { ...event, event: "cancelled" });
          }
        } else {
          if (event.event === "started" && selections.has(id)) {
            selections.get(id).active = true;
            publish({ event: "started", requestId: id });
          } else if (["stopped", "cancelled"].includes(event.event)) selections.delete(id);
          ipc.send("dpe-capture-event", event);
        }
      },
    );
    ipc.on("dpe-capture-ended", (_event, value) => {
      if (!Number.isSafeInteger(value?.requestId)) return;
      const selection = selections.get(value.requestId);
      if (selection?.active) {
        selections.delete(value.requestId);
        // The native capture owns its FD after started. Let its connection clear
        // the source, never cancel/free that live constructor from this bridge.
        publish({ event: "ended", requestId: value.requestId });
      } else cancelSelection(value.requestId);
    });
    refreshMethods();
    const release = voice?.releaseNativeDesktopVideoSourcePickerStream;
    if (typeof release === "function")
      voice.releaseNativeDesktopVideoSourcePickerStream = function (...args) {
        // Preserve the normal portal release, then retire our exact requests.
        // The native release itself does not clear the selected configuration.
        const result = Reflect.apply(release, this, args);
        for (const id of selections.keys()) cancelSelection(id);
        return result;
      };
  } catch (error) {
    console.error("DPE capture backends:", error.message);
  }
}
function setRate(value) {
  if (!Number.isInteger(value) || value < 1 || value > 240 || fps === value) return;
  fps = value;
  ipc?.send("dpe-capture-rate", fps);
}
module.exports = {
  install,
  subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  setRate,
  methods: () => {
    refreshMethods();
    return adapter ? [...available] : [];
  },
  reasons: () => (adapter ? { ...methodReasons } : {}),
  snapshot() {
    const active = [...selections.values()].filter((selection) => selection.active);
    return active.length === 1
      ? {
          requestId: active[0].requestId,
          method: active[0].method,
          sourceType: active[0].sourceType,
          handoff: active[0].handoff,
          handoffFallback: active[0].handoffFallback,
        }
      : null;
  },
};
