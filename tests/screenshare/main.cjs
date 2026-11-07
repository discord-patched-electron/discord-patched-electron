const { app, BrowserWindow, desktopCapturer, ipcMain, session } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const root = process.env.DPE_SCREENSHARE_OUTPUT;
const config = JSON.parse(fs.readFileSync(path.join(root, "config.json"), "utf8"));
app.setPath("userData", path.join(root, "profile"));
app.commandLine.appendSwitch("disable-background-networking");
let win, receiverWindow;
let nextReceiverRequest = 0;
const receiverRequests = new Map();
let finished = false;
function recordPeers() {
  if (!config.split_peers || !win || !receiverWindow) return;
  const own = win.webContents.getOSProcessId();
  const receiver = receiverWindow.webContents.getOSProcessId();
  if (own === receiver || own <= 0 || receiver <= 0) {
    finish({ passed: false, error: "Peers did not receive independent processes" });
    return;
  }
  fs.writeFileSync(
    path.join(root, "peer-processes.json"),
    JSON.stringify(
      {
        pidNamespace: fs.readlinkSync("/proc/self/ns/pid"),
        sender: own,
        receiver,
        browser: process.pid,
        sharedGpu: app
          .getAppMetrics()
          .filter((p) => p.type === "GPU")
          .map((p) => p.pid),
      },
      null,
      2,
    ) + "\n",
  );
}
function finish(report) {
  if (finished) return;
  finished = true;
  recordPeers();
  // Availability context only. Actual native receive stats determine whether
  // the requested Electron decoder delivered frames or fell back.
  report.chromiumGpuFeatureStatus = app.getGPUFeatureStatus();
  fs.writeFileSync(path.join(root, "report.json"), JSON.stringify(report, null, 2) + "\n");
  app.exit(report.passed ? 0 : 1);
}
ipcMain.handle("receiver-call", (event, method, args) => {
  if (
    !config.split_peers ||
    event.sender !== win?.webContents ||
    !receiverWindow ||
    receiverRequests.size >= 64 ||
    typeof method !== "string" ||
    !Array.isArray(args)
  )
    throw Error("Invalid receiver RPC");
  return new Promise((resolve, reject) => {
    const id = ++nextReceiverRequest;
    const timer = setTimeout(() => {
      receiverRequests.delete(id);
      reject(Error("Receiver RPC timed out"));
    }, 12000);
    receiverRequests.set(id, { resolve, reject, timer });
    receiverWindow.webContents.send("receiver-command", id, method, args);
  });
});
ipcMain.on("receiver-result", (event, id, value, error) => {
  if (event.sender !== receiverWindow?.webContents) return;
  const pending = receiverRequests.get(id);
  if (!pending) return;
  receiverRequests.delete(id);
  clearTimeout(pending.timer);
  if (error) pending.reject(Error(error));
  else pending.resolve(value);
});
ipcMain.on("receiver-event", (event, name, value) => {
  if (event.sender === receiverWindow?.webContents)
    win.webContents.send("receiver-event", name, value);
});
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
    if (config.split_peers) {
      const receiverSession = session.fromPartition("dpe-fixture-receiver");
      receiverSession.webRequest.onBeforeRequest((details, callback) =>
        callback({ cancel: !details.url.startsWith("data:") }),
      );
      receiverSession.setPermissionRequestHandler((_, __, callback) => callback(false));
      receiverWindow = new BrowserWindow({
        show: false,
        width: 320,
        height: 180,
        webPreferences: {
          preload: path.join(__dirname, "receiver.cjs"),
          session: receiverSession,
          sandbox: false,
          contextIsolation: false,
          backgroundThrottling: false,
        },
      });
      receiverWindow.webContents.on("preload-error", (_, __, error) =>
        finish({ passed: false, error: String(error) }),
      );
      receiverWindow.webContents.on("render-process-gone", (_, details) =>
        finish({ passed: false, error: details }),
      );
      await receiverWindow.loadURL("data:text/html,<body>synthetic receiver</body>");
    }
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
    win.webContents.on("console-message", (event) => console.log(event.message));
    win.webContents.on("preload-error", (_, __, error) =>
      finish({ passed: false, error: String(error) }),
    );
    win.webContents.on("render-process-gone", (_, details) =>
      finish({ passed: false, error: details }),
    );
    win.webContents.on("did-finish-load", recordPeers);
    await win.loadURL(
      `data:text/html,<body style='margin:0;overflow:hidden;background:%23222'><canvas style='display:block;width:100vw;height:100vh' width=${config.width} height=${config.height}></canvas>`,
    );
  })
  .catch((error) => finish({ passed: false, error: String(error) }));
setTimeout(
  () => finish({ passed: false, error: "Fixture timeout" }),
  (config.seconds + (config.baseline_seconds || 0) + 15) * 1000,
);
