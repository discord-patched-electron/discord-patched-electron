const { app, BrowserWindow, ipcMain, session } = require("electron");
const fs = require("node:fs"),
  path = require("node:path");
app.setPath("userData", path.join(process.env.DPE_AUDIO_OUTPUT, "profile"));
let finished = false;
function finish(result) {
  if (finished) return;
  finished = true;
  fs.writeFileSync(
    path.join(process.env.DPE_AUDIO_OUTPUT, "fixture.json"),
    JSON.stringify(
      {
        ...result,
        versions: process.versions,
        stage: fs.existsSync(path.join(process.env.DPE_AUDIO_OUTPUT, "stage.json"))
          ? JSON.parse(fs.readFileSync(path.join(process.env.DPE_AUDIO_OUTPUT, "stage.json")))
          : null,
      },
      null,
      2,
    ),
  );
  app.exit(result.passed ? 0 : 1);
}
ipcMain.on("audio-result", (_e, result) => finish(result));
app.whenReady().then(() => {
  session.defaultSession.webRequest.onBeforeRequest((d, cb) =>
    cb({ cancel: !d.url.startsWith("file:") }),
  );
  const w = new BrowserWindow({
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      sandbox: false,
    },
  });
  w.webContents.on("console-message", (d) => console.log(d.message));
  w.webContents.on("preload-error", (_e, _p, error) =>
    finish({ passed: false, error: String(error) }),
  );
  w.webContents.on("render-process-gone", (_e, details) =>
    finish({ passed: false, error: details }),
  );
  w.loadFile(path.join(__dirname, "index.html"));
});
setTimeout(
  () => finish({ passed: false, error: "Wayland fixture timed out" }),
  process.env.DPE_FIXTURE_SELECTION === "interactive" ? 600000 : 90000,
);
