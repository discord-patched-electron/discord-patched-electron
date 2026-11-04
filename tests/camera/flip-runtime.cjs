// Real packaged voice wrapper and Node-API helper in a fresh Electron renderer.
// Initialize fake devices only; never start capture, connect, or load Discord.
const { app, BrowserWindow, ipcMain, session } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const output = process.env.DPE_FLIP_TEST_OUTPUT;
const root = process.env.DPE_APP_ROOT;
if (!output || !root || !process.env.DPE_PROFILE_DIR) throw Error("Test paths are required");
app.setPath("userData", process.env.DPE_PROFILE_DIR);
const values = { webcamFlip: true };
fs.mkdirSync(process.env.DPE_PROFILE_DIR, { recursive: true });
fs.writeFileSync(
  path.join(process.env.DPE_PROFILE_DIR, "settings.json"),
  JSON.stringify({ openasar: { desktop: values } }),
);
const controller = require(path.join(root, "integration/desktop/preferences.cjs")).install(
  require("electron"),
  { getConfig: () => values },
);
const transitions = [];
function finish(error) {
  fs.writeFileSync(
    path.join(output, "runtime.json"),
    JSON.stringify(
      { passed: !error, error: error?.message, transitions, versions: process.versions },
      null,
      2,
    ),
  );
  app.exit(error ? 1 : 0);
}
ipcMain.on("dpe-flip-test-error", (_event, error) => finish(Error(error)));
ipcMain.on("dpe-camera-flip-status", async (_event, value) => {
  try {
    if (!value.supported) throw Error("Packaged native camera hook is unavailable");
    const expected = [true, false, true, false][transitions.length];
    if (value.active !== expected) return;
    transitions.push(value.active);
    if (transitions.length === 4) finish();
    else await controller.apply({ webcamFlip: !value.active });
  } catch (error) {
    finish(error);
  }
});
app
  .whenReady()
  .then(async () => {
    session.defaultSession.setPermissionRequestHandler((_web, _permission, respond) =>
      respond(false),
    );
    session.defaultSession.webRequest.onBeforeRequest((_details, respond) =>
      respond({ cancel: true }),
    );
    const window = new BrowserWindow({
      show: false,
      webPreferences: {
        preload: path.join(__dirname, "flip-runtime-preload.cjs"),
        sandbox: false,
        contextIsolation: true,
      },
    });
    window.webContents.on("preload-error", (_event, _file, error) => finish(error));
    window.webContents.on("render-process-gone", (_event, details) =>
      finish(Error(details.reason)),
    );
    await window.loadURL("about:blank");
  })
  .catch(finish);
setTimeout(() => finish(Error("Camera settings runtime test timed out")), 15000);
