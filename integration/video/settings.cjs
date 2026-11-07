"use strict";
const fs = require("node:fs");
const path = require("node:path");

const DEFAULTS = Object.freeze({
  captureMethod: "auto",
  capturePacing: true,
  experimentalCapture: false,
  preserveViewerBitrate: false,
  excludedCodecs: Object.freeze([]),
});
const METHODS = ["auto", "portal", "x11", "wayland-direct", "vkcapture", "kms"];
const CODECS = ["AV1", "H265", "H264", "VP8"];
function normalize(value) {
  const excludedCodecs = CODECS.filter(
    (codec) => Array.isArray(value?.excludedCodecs) && value.excludedCodecs.includes(codec),
  );
  return {
    captureMethod: METHODS.includes(value?.captureMethod) ? value.captureMethod : "auto",
    capturePacing: value?.capturePacing !== false,
    experimentalCapture: value?.experimentalCapture === true,
    preserveViewerBitrate: value?.preserveViewerBitrate === true,
    // A hand-edited profile cannot disable every advertised choice.
    excludedCodecs: excludedCodecs.length < CODECS.length ? excludedCodecs : [],
  };
}
function read() {
  try {
    const value = JSON.parse(
      fs.readFileSync(path.join(process.env.DPE_PROFILE_DIR, "settings.json"), "utf8"),
    );
    return normalize(value?.openasar?.screensharing);
  } catch {
    return { ...DEFAULTS };
  }
}

let installed = false;
function install({ snapshot, methods = ["auto"], methodReasons }, ipc) {
  if (installed) return;
  try {
    ipc ||= require("electron").ipcRenderer;
  } catch {
    return;
  }
  if (typeof ipc?.on !== "function" || typeof ipc?.send !== "function") return;
  installed = true;
  let codecExclusionError = null;
  globalThis.addEventListener?.("dpe-codec-exclusion-status", (event) => {
    codecExclusionError = event.detail === "no-allowed-encoder" ? event.detail : null;
  });
  let pending = false;
  ipc.on("dpe-screenshare-status-request", async (_event, request) => {
    if (pending || typeof request !== "string" || !/^[a-f0-9-]{36}$/.test(request)) return;
    pending = true;
    try {
      // The provider owns the whitelist. Never send native getStats objects,
      // source IDs, application titles, users or connection credentials.
      ipc.send("dpe-screenshare-status-response", request, {
        ...(await snapshot()),
        codecExclusionError,
        methods: typeof methods === "function" ? methods() : methods,
        ...(methodReasons
          ? { methodReasons: typeof methodReasons === "function" ? methodReasons() : methodReasons }
          : {}),
      });
    } catch {
      ipc.send("dpe-screenshare-status-response", request, { error: true });
    } finally {
      pending = false;
    }
  });
}

module.exports = { DEFAULTS, METHODS, CODECS, normalize, read, install };
