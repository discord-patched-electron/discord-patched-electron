const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");

function load(directory, helper = path.join(process.env.DPE_APP_ROOT, "libdpe-camera-probe.so")) {
  const metadata = JSON.parse(fs.readFileSync(path.join(directory, "camera-flip.json"), "utf8"));
  const filename = path.join(directory, "discord_voice.node");
  if (
    metadata.supported !== true ||
    !Number.isSafeInteger(metadata.call) ||
    metadata.call <= 0 ||
    metadata.call > 0xffffffff ||
    !Number.isSafeInteger(metadata.converter) ||
    metadata.converter <= 0 ||
    metadata.converter > 0xffffffff ||
    createHash("sha256").update(fs.readFileSync(filename)).digest("hex") !== metadata.voice_sha256
  )
    throw new Error("This native camera module does not support outgoing mirroring.");
  const addon = { exports: {} };
  process.dlopen(addon, helper);
  addon.exports.install(filename, metadata.call, metadata.converter);
  return addon.exports;
}

let installed = false;
function install(directory) {
  if (installed) return;
  installed = true;
  const { ipcRenderer } = require("electron");
  if (!ipcRenderer) return;
  let addon;
  try {
    addon = load(directory);
  } catch (error) {
    console.error("DPE camera flip:", error.message);
  }
  const apply = (value) => {
    const active = value === true && Boolean(addon);
    addon?.setEnabled(active);
    ipcRenderer.send("dpe-camera-flip-status", { supported: Boolean(addon), active });
  };
  // Subscribe before reading preferences, so a save during startup is not lost.
  let revision = 0;
  ipcRenderer.on("dpe-camera-flip", (_event, value) => {
    revision++;
    apply(value);
  });
  // Initialize synchronously before native camera startup; the main-process
  // refresh below catches saves that preceded listener registration.
  let saved = false;
  try {
    saved =
      JSON.parse(fs.readFileSync(path.join(process.env.DPE_PROFILE_DIR, "settings.json"), "utf8"))
        .openasar?.desktop?.webcamFlip === true;
  } catch {
    /* A new profile uses the unchanged outgoing image. */
  }
  apply(saved);
  ipcRenderer
    .invoke("dpe-camera-flip-load")
    .then((value) => {
      if (revision === 0) apply(value);
    })
    .catch(() => {});
}
module.exports = { install, load };
