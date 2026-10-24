const { app, BrowserWindow, ipcMain, session } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const profile = process.env.DPE_PROBE_PROFILE;
const report = process.env.DPE_PROBE_REPORT;
if (!profile || !report || !process.env.DPE_PROBE_MODULES) {
  throw new Error("Isolated fixture paths are required");
}
app.setPath("userData", profile);
app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");
app.commandLine.appendSwitch("disable-background-networking");
app.commandLine.appendSwitch("disable-renderer-backgrounding");
app.commandLine.appendSwitch("js-flags", "--expose-gc");
let window;
let finished = false;
let progress = null;
function finish(result) {
  if (finished) return;
  finished = true;
  fs.writeFileSync(
    report,
    JSON.stringify(
      {
        schema: 1,
        fixture: "discord-native-bridge",
        versions: process.versions,
        userData: app.getPath("userData"),
        progress,
        ...result,
        real_discord_call_verified: false,
        krisp_suppression_verified: false,
        per_app_audio_verified: false,
        publishable: false,
      },
      null,
      2,
    ) + "\n",
  );
  if (window && !window.isDestroyed()) window.destroy();
  app.exit(result.passed ? 0 : 1);
}
ipcMain.on("fixture-result", (_event, result) => finish(result));
ipcMain.on("fixture-progress", (_event, value) => {
  progress = value;
  console.log("fixture-progress", value);
});
app
  .whenReady()
  .then(async () => {
    // No remote UI, telemetry, remote resources or account login in this fixture.
    session.defaultSession.webRequest.onBeforeRequest((details, callback) => {
      callback({ cancel: !/^(file:|data:|devtools:)/.test(details.url) });
    });
    session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) =>
      callback(false),
    );
    window = new BrowserWindow({
      width: 320,
      height: 240,
      show: true,
      webPreferences: {
        preload: path.join(__dirname, "preload.cjs"),
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: false,
        backgroundThrottling: false,
      },
    });
    window.webContents.on("render-process-gone", (_event, details) =>
      finish({ passed: false, renderer_exit: details }),
    );
    window.webContents.on("console-message", (details) =>
      console.log("renderer-console", details.message),
    );
    window.webContents.on("preload-error", (_event, _path, error) =>
      finish({ passed: false, error: error.stack }),
    );
    await window.loadFile(path.join(__dirname, "index.html"));
  })
  .catch((error) => finish({ passed: false, error: error.stack }));
setTimeout(
  () => finish({ passed: false, error: "Fixture timed out" }),
  process.env.DPE_PROBE_AUDIO_INPUT ? 80000 : 25000,
);
