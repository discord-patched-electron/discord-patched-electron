const { app, BrowserWindow, desktopCapturer, ipcMain, session } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const root = process.env.DPE_SCREENSHARE_OUTPUT;
const config = JSON.parse(fs.readFileSync(path.join(root, "config.json"), "utf8"));
app.setPath("userData", path.join(root, "profile"));
app.commandLine.appendSwitch("disable-background-networking");
let win;
let finished = false;
function finish(report) {
  if (finished) return;
  finished = true;
  // Availability context only. Actual native receive stats determine whether
  // the requested Electron decoder delivered frames or fell back.
  report.chromiumGpuFeatureStatus = app.getGPUFeatureStatus();
  fs.writeFileSync(path.join(root, "report.json"), JSON.stringify(report, null, 2) + "\n");
  app.exit(report.passed ? 0 : 1);
}
ipcMain.on("report", (_, report) => finish(report));
ipcMain.handle("source", async () => {
  if (config.backend === "pipewire") return "screen:0:0";
  if (config.source === "window") return String(win.getNativeWindowHandle().readUInt32LE());
  const sources = await desktopCapturer.getSources({
    types: ["screen"],
    thumbnailSize: { width: 0, height: 0 },
  });
  // Native Linux capture takes the numeric XRandR ID, not Electron's screen:ID:0.
  const id = /^screen:(\d+):/.exec(sources[0]?.id || "")?.[1];
  if (!id) throw Error("Private desktop source was not found");
  return id;
});
app
  .whenReady()
  .then(async () => {
    session.defaultSession.webRequest.onBeforeRequest((details, callback) =>
      callback({ cancel: !details.url.startsWith("data:") }),
    );
    session.defaultSession.setPermissionRequestHandler((_, __, callback) => callback(false));
    win = new BrowserWindow({
      show: config.display_backend !== "wayland",
      frame: false,
      width: config.width,
      height: config.height,
      x: 0,
      y: 0,
      webPreferences: {
        preload: path.join(__dirname, "preload.cjs"),
        sandbox: false,
        contextIsolation: false,
        backgroundThrottling: false,
      },
    });
    win.webContents.on("preload-error", (_, __, error) =>
      finish({ passed: false, error: String(error) }),
    );
    win.webContents.on("render-process-gone", (_, details) =>
      finish({ passed: false, error: details }),
    );
    await win.loadURL(
      `data:text/html,<body style='margin:0;overflow:hidden;background:%23222'><canvas style='display:block;width:100vw;height:100vh' width=${config.width} height=${config.height}></canvas>`,
    );
  })
  .catch((error) => finish({ passed: false, error: String(error) }));
setTimeout(() => finish({ passed: false, error: "Fixture timeout" }), (config.seconds + 15) * 1000);
