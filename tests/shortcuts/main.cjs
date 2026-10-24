const { app, BrowserWindow, ipcMain } = require("electron");
const fs = require("node:fs"),
  path = require("node:path");
const output = process.env.DPE_SHORTCUT_OUTPUT;
app.setPath("userData", path.join(output, "profile"));
let done = false;
function finish(result) {
  if (done) return;
  done = true;
  fs.writeFileSync(
    path.join(output, "shortcuts.json"),
    JSON.stringify({ ...result, versions: process.versions }, null, 2),
  );
  app.exit(result.passed ? 0 : 1);
}
ipcMain.on("result", (_e, r) => finish(r));
app.whenReady().then(async () => {
  const window = new BrowserWindow({
    show: false,
    webPreferences: {
      sandbox: false,
      contextIsolation: true,
      preload: path.join(__dirname, "preload.cjs"),
    },
  });
  window.webContents.on("preload-error", (_e, _p, error) =>
    finish({ passed: false, error: String(error) }),
  );
  await window.loadURL("data:text/html,<title>Isolated shortcut fixture</title>");
});
setTimeout(() => finish({ passed: false, error: "Native portal shortcut timed out" }), 18000);
