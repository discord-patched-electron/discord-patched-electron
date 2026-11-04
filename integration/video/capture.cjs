"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");

function load(
  directory,
  helper,
  {
    mode = "observe",
    upstreamEnabled = process.env.DPE_CAPTURE_UPSTREAM !== "0",
    upstreamMode = process.env.DPE_CAPTURE_UPSTREAM === "1" ? "runtime" : "initial",
  } = {},
) {
  if (!["observe", "latest", "paced"].includes(mode))
    throw new Error("Unknown capture adapter mode.");
  const metadata = JSON.parse(fs.readFileSync(path.join(directory, "capture-scope.json"), "utf8"));
  const filename = path.join(directory, "discord_voice.node");
  const offsets = [
    metadata.dequeue?.call,
    metadata.dequeue?.target,
    metadata.release?.call,
    metadata.release?.target,
  ];
  if (
    metadata.schema !== 1 ||
    metadata.supported !== true ||
    offsets.some((value) => !Number.isSafeInteger(value) || value <= 0 || value > 0xffffffff) ||
    createHash("sha256").update(fs.readFileSync(filename)).digest("hex") !== metadata.voice_sha256
  )
    throw new Error("The native capture module does not match the reviewed adapter.");
  const addon = { exports: {} };
  process.dlopen(addon, helper);
  if (typeof addon.exports.installCapture !== "function")
    throw new Error("Capture support is unavailable in this helper.");
  addon.exports.installCapture(filename, ...offsets, mode !== "observe");
  let upstream;
  // Initial preferences preserve the producer's full acceptable format range.
  // Runtime renegotiation remains experimental and requires explicit opt-in.
  if (mode === "paced" && upstreamEnabled) {
    try {
      upstream = require("./dpe-capture-upstream.cjs").load(directory, helper, {
        mode: upstreamMode,
      });
    } catch (error) {
      // Older helpers or unreviewed linkages retain the downstream frame gate.
      console.error("DPE upstream capture:", error.message);
    }
  }
  function upstreamCall(name, ...args) {
    if (!upstream) return null;
    try {
      return upstream[name](...args);
    } catch (error) {
      // Optional producer control must not change a successful native call's
      // return value. Restore original offers if the helper is still callable.
      const failed = upstream;
      upstream = null;
      try {
        failed.setRate(0);
      } catch {}
      console.error("DPE upstream capture:", error.message);
      return null;
    }
  }
  return {
    snapshot: () => ({
      ...JSON.parse(addon.exports.captureStats()),
      upstream: upstreamCall("snapshot"),
    }),
    setRate: (fps) => {
      const rate = mode === "paced" ? fps : 0;
      const result = addon.exports.setCaptureRate(rate);
      upstreamCall("setRate", rate);
      return result;
    },
    mode,
  };
}

let initialized = false,
  adapter;
function install(directory) {
  if (initialized) return;
  initialized = true;
  // The runtime enables pacing only for an active share with a known requested
  // frame rate. Unknown native versions fail closed at the scope/hash check.
  const mode = process.env.DPE_CAPTURE_PIPEWIRE ?? "paced";
  if (process.platform !== "linux" || !["observe", "latest", "paced"].includes(mode)) return;
  try {
    adapter = load(directory, path.join(process.env.DPE_APP_ROOT, "libdpe-camera-probe.so"), {
      mode,
    });
  } catch (error) {
    console.error("DPE capture:", error.message);
  }
}

module.exports = {
  load,
  install,
  snapshot: () => adapter?.snapshot() ?? null,
  setRate: (fps) => adapter?.setRate(fps),
};
