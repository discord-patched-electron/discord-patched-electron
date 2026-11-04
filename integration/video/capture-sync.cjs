"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");

function load(directory, helper) {
  const metadata = JSON.parse(
    fs.readFileSync(path.join(directory, "capture-sync-scope.json"), "utf8"),
  );
  const filename = path.join(directory, "discord_voice.node");
  const offsets = [metadata.call, metadata.target];
  if (
    metadata.schema !== 1 ||
    metadata.supported !== true ||
    offsets.some((value) => !Number.isSafeInteger(value) || value <= 0 || value > 0xffffffff) ||
    createHash("sha256").update(fs.readFileSync(filename)).digest("hex") !== metadata.voice_sha256
  )
    throw new Error(
      "The native module does not match the reviewed capture synchronization adapter.",
    );
  const addon = { exports: {} };
  process.dlopen(addon, helper);
  for (const name of ["installCaptureSync", "captureSyncStats"])
    if (typeof addon.exports[name] !== "function")
      throw new Error("capture synchronization support is unavailable in this helper.");
  addon.exports.installCaptureSync(filename, ...offsets);
  return { snapshot: () => JSON.parse(addon.exports.captureSyncStats()) };
}
let initialized = false,
  adapter;
function install(directory) {
  if (initialized) return;
  initialized = true;
  if (process.platform !== "linux") return;
  try {
    adapter = load(directory, path.join(process.env.DPE_APP_ROOT, "libdpe-camera-probe.so"));
  } catch (error) {
    console.error("DPE capture synchronization:", error.message);
  }
}
module.exports = { load, install, snapshot: () => adapter?.snapshot() ?? null };
