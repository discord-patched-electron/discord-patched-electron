const { app, BrowserWindow, ipcMain, safeStorage } = require("electron");
const fs = require("node:fs"),
  path = require("node:path");
const output = process.env.DPE_STORAGE_OUTPUT;
app.setPath("userData", path.join(output, "profile"));
app.commandLine.appendSwitch("password-store", "basic"); // Disposable synthetic fixture only.
safeStorage.setUsePlainTextEncryption(true);
const events = Object.fromEntries(
  ["IS_ENCRYPTION_AVAILABLE", "ENCRYPT_STRING", "DECRYPT_STRING"].map((n) => [
    "SAFE_STORAGE_" + n,
    n,
  ]),
);
require(process.env.DPE_STORAGE_ADAPTER).install({ ipcMain, safeStorage }, events);
let done = false;
function finish(value) {
  if (done) return;
  done = true;
  fs.writeFileSync(
    path.join(output, "storage.json"),
    JSON.stringify({ ...value, versions: process.versions }, null, 2),
  );
  app.exit(value.passed ? 0 : 1);
}
ipcMain.on("result", (_e, result) => finish(result));
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
  await window.loadURL("data:text/html,<title>Storage compatibility check</title>");
});
setTimeout(
  () => finish({ passed: false, error: "Synchronous IPC did not receive a reply" }),
  15000,
);
