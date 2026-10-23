const { app, BrowserWindow, ipcMain, session } = require("electron");
const fs = require("node:fs"),
  path = require("node:path");
const output = process.env.DPE_CAMERA_OUTPUT;
app.setPath("userData", path.join(output, "profile"));
let finished = false;
function finish(result) {
  if (finished) return;
  finished = true;
  fs.writeFileSync(
    path.join(output, "result.json"),
    JSON.stringify({ ...result, versions: process.versions }, null, 2),
  );
  app.exit(result.passed ? 0 : 1);
}
ipcMain.on("camera-result", (_event, result) => finish(result));
app.whenReady().then(() => {
  session.defaultSession.webRequest.onBeforeRequest((details, callback) =>
    callback({ cancel: !details.url.startsWith("file:") }),
  );
  const window = new BrowserWindow({
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      sandbox: false,
      contextIsolation: true,
    },
  });
  window.webContents.on("preload-error", (_event, _path, error) =>
    finish({ passed: false, error: String(error) }),
  );
  window.webContents.on("render-process-gone", (_event, details) =>
    finish({ passed: false, error: details.reason }),
  );
  window.loadFile(path.join(__dirname, "index.html"));
});
setTimeout(
  () => finish({ passed: false, error: "Camera probe timed out" }),
  30000,
);
