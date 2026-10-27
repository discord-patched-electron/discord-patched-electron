const { app, BrowserWindow, dialog } = require("electron");
const { execFile } = require("node:child_process");
const path = require("node:path");
const preferences = require("./profile.cjs");
const relaunch = require("./relaunch.cjs");
let window;

function resolve(config, { recoverInvalid = false } = {}) {
  return new Promise((resolve, reject) => {
    const child = execFile(
      "python3",
      [
        "-I",
        path.join(process.env.DPE_APP_ROOT, "integration/launcher/flags.py"),
        "preview",
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
      }),
    );
  });
}

function open() {
  if (window && !window.isDestroyed()) {
    window.show();
    window.focus();
    return;
  }
  require("./window.cjs").register();
  window = new BrowserWindow({
    title: "Launch presets — Discord",
    width: 880,
    height: 820,
    minWidth: 360,
    minHeight: 360,
    backgroundColor: "#202225",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "launch-settings-preload.cjs"),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  const current = window;
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
  handle("dpe-launch-save", async (config) => {
    const result = await resolve(config);
    if (result.error) return result;
    const settings = global.settings;
    if (!settings)
      throw new Error("Discord settings are not ready. Reopen the editor after startup.");
    const oa = { ...settings.get("openasar", {}), launchFlags: result.config };
    settings.set("openasar", oa);
    settings.save();
    global.oaConfig = { ...oa, autoupdate: false };
    return result;
  });
  handle("dpe-launch-restart", () => {
    // This is explicitly labelled "Restart with saved flags" in recovery mode.
    relaunch.restart(app, { normal: true });
    return { restarting: true };
  });
  current.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  current.webContents.on("will-navigate", (event) => event.preventDefault());
  current.webContents.on("will-prevent-unload", (event) => {
    const response = dialog.showMessageBoxSync(current, {
      type: "question",
      buttons: ["Keep editing", "Discard changes"],
      defaultId: 0,
      cancelId: 0,
      title: "Unsaved launch settings",
      message: "Discard your unsaved launch settings?",
    });
    if (response === 1) event.preventDefault();
  });
  current.once("closed", () => {
    window = null;
  });
  void current.loadURL("dpe-splash://app/launch-settings.html").catch(console.error);
}
module.exports = { open, resolve };
