"use strict";
const fs = require("node:fs");
const path = require("node:path");
const nativeIdentity = require("./dpe-native-identity.cjs");

function load(directory, helper, { mode = "initial" } = {}) {
  if (mode !== "initial" && mode !== "runtime") throw new Error("Unknown upstream capture mode.");
  const metadata = JSON.parse(
    fs.readFileSync(path.join(directory, "capture-upstream-scope.json"), "utf8"),
  );
  const filename = path.join(directory, "discord_voice.node");
  const offsets = [metadata.connect, metadata.update].flatMap((entry) => [
    entry?.slot,
    entry?.target,
    entry?.call,
  ]);
  if (
    metadata.schema !== 1 ||
    metadata.supported !== true ||
    offsets.some((value) => !Number.isSafeInteger(value) || value <= 0 || value > 0xffffffff) ||
    !nativeIdentity.matches(filename, metadata.voice_sha256)
  )
    throw new Error("The native module does not match the reviewed upstream capture adapter.");
  const addon = { exports: {} };
  process.dlopen(addon, helper);
  for (const name of ["installCaptureUpstreamV2", "setCaptureUpstreamRate", "captureUpstreamStats"])
    if (typeof addon.exports[name] !== "function")
      throw new Error("Upstream capture support is unavailable in this helper.");
  addon.exports.installCaptureUpstreamV2(filename, ...offsets, mode === "runtime" ? 1 : 0);
  let requestedFps = 0;
  return {
    setRate(fps) {
      if (!Number.isInteger(fps) || fps < 0 || fps > 240)
        throw new Error("Upstream capture rate must be an integer from 0 through 240.");
      // Current Discord quality choices, including Source, top out at 60 FPS.
      // Reserve that cadence so an in-place 30→60 change does not get stranded
      // by an initial producer preference. Unknown/off remains untouched.
      const preferred = mode === "initial" && fps > 0 ? Math.max(60, fps) : fps;
      const result = addon.exports.setCaptureUpstreamRate(preferred);
      requestedFps = fps;
      return result;
    },
    snapshot: () => ({ ...JSON.parse(addon.exports.captureUpstreamStats()), requestedFps }),
  };
}

module.exports = { load };
