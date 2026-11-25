const { app } = require("electron");
const { execFile } = require("node:child_process");
const path = require("node:path");
const preferences = require("./profile.cjs");
const relaunch = require("./relaunch.cjs");
const closing = new WeakMap();

async function confirmClose(window) {
  const state = closing.get(window);
  if (state?.saving) {
    state.closeRequested = true;
    return false;
  }
  if (!state?.dirty || state.discardApproved) return true;
  if (!state.confirmation) {
    state.confirmation = new Promise((resolve) => {
      state.finishConfirmation = (discard) => {
        state.discardApproved = discard && !state.saving;
        state.confirmation = state.finishConfirmation = undefined;
        resolve(state.discardApproved);
      };
    });
    window.webContents.send("dpe-launch-confirm-discard");
  }
  return state.confirmation;
}

function resetCloseApproval(window) {
  const state = closing.get(window);
  if (state) state.discardApproved = false;
}

function resolve(config, { recoverInvalid = false, save = false, fileRevision } = {}) {
  return new Promise((resolve, reject) => {
    const child = execFile(
      "python3",
      [
        "-I",
        path.join(process.env.DPE_APP_ROOT, "integration/launcher/flags.py"),
        save ? "save" : "preview",
        process.env.DPE_PROFILE_DIR,
        process.env.DPE_PACKAGE_NAME || "discord-canary-prerelease-patched-electron-bin",
        process.env.DPE_DESKTOP_ID || "discord-canary",
      ],
      { timeout: 5000, maxBuffer: 512 * 1024, env: process.env },
      (error, stdout) => {
        if (error)
          return reject(
            new Error(
              "Could not resolve launch flags. Check that Python 3 and the launcher files are installed.",
            ),
          );
        try {
          resolve(JSON.parse(stdout));
        } catch {
          reject(new Error("The flag resolver returned an invalid response."));
        }
      },
    );
    child.stdin.on("error", () => {});
    child.stdin.end(
      JSON.stringify({
        settings: global.settings?.get
          ? { ...preferences.settings(), openasar: global.settings.get("openasar", {}) }
          : preferences.settings(),
        ...(config === undefined ? {} : { config }),
        args: relaunch.arguments().filter((arg) => arg !== "--ignore-flags"),
        recoverInvalid,
        fileRevision,
      }),
    );
  });
}

// Share the existing OpenAsar window and its sandboxed main-frame IPC.
function attach(current) {
  const state = { dirty: false, saving: false, closeRequested: false, discardApproved: false };
  closing.set(current, state);
  current.webContents.ipc.on("dpe-launch-dirty", (event, dirty) => {
    if (
      event.sender !== current.webContents ||
      event.senderFrame !== current.webContents.mainFrame ||
      typeof dirty !== "boolean"
    )
      return;
    if (state.dirty !== dirty) state.discardApproved = false;
    state.dirty = dirty;
  });
  current.webContents.ipc.on("dpe-launch-saving", (event, saving) => {
    if (
      event.sender !== current.webContents ||
      event.senderFrame !== current.webContents.mainFrame ||
      typeof saving !== "boolean"
    )
      return;
    state.saving = saving;
    if (!saving && state.closeRequested) {
      state.closeRequested = false;
      if (!state.dirty && !current.isDestroyed()) current.close();
    }
  });
  current.webContents.ipc.on("dpe-launch-discard-decision", (event, discard) => {
    if (
      event.sender === current.webContents &&
      event.senderFrame === current.webContents.mainFrame &&
      typeof discard === "boolean"
    )
      state.finishConfirmation?.(discard);
  });
  current.once("closed", () => state.finishConfirmation?.(false));
  current.webContents.once("render-process-gone", () => state.finishConfirmation?.(false));
  const handle = (name, callback) =>
    current.webContents.ipc.handle(name, async (event, ...args) => {
      if (
        event.sender !== current.webContents ||
        event.senderFrame !== current.webContents.mainFrame
      )
        throw new Error("Launch settings are available only in the local settings window.");
      try {
        return await callback(...args);
      } catch (error) {
        return { error: error.message };
      }
    });
  handle("dpe-launch-load", () => resolve(undefined, { recoverInvalid: true }));
  handle("dpe-launch-preview", (config) => resolve(config));
  handle("dpe-launch-save", async (config, fileRevision) => {
    const settings = global.settings;
    if (!settings)
      throw new Error("Discord settings are not ready. Reopen the editor after startup.");
    const result = await resolve(config, { save: true, fileRevision });
    if (result.error) return result;
    const oa = { ...settings.get("openasar", {}) };
    for (const key of ["launchFlags", "customFlags", "cmdPreset"]) delete oa[key];
    settings.set("openasar", oa);
    try {
      settings.save();
    } catch (error) {
      // The canonical file is already saved; don't report that write as failed.
      result.warnings.push(`Flags saved, but legacy settings cleanup failed: ${error.message}`);
    }
    global.oaConfig = { ...oa, autoupdate: false };
    return result;
  });
  handle("dpe-launch-restart", () => {
    if (state.saving || state.dirty)
      return { error: "Wait for flags to save, or fix the flag error before restarting." };
    const mods = require("./mod-settings.cjs");
    if (mods.isOnboardingSettings(current)) return mods.finishSettings(current);
    if (mods.isOnboarding()) {
      const error = "Close Settings to finish setup and restart Discord.";
      current.webContents.send("dpe-setup-reminder", error);
      return { error };
    }
    // Restart from the editor leaves recovery after the saved file is repaired.
    relaunch.restart(app, { normal: true });
    return { restarting: true };
  });
  current.webContents.on("will-prevent-unload", (event) => {
    if (state.discardApproved) {
      event.preventDefault();
      return;
    }
    if (state.confirmation) return;
    state.dirty = true;
    // Keep the renderer alive for the confirmation, then retry the close.
    void confirmClose(current).then((allowed) => {
      if (allowed && !current.isDestroyed()) current.close();
    });
  });
}
function open() {
  return require("./config.cjs").open("flags");
}
module.exports = { open, attach, resolve, confirmClose, resetCloseApproval };
