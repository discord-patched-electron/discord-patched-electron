const { BrowserWindow, ipcMain, net, protocol } = require("electron");
const path = require("node:path");
const assetHandler = require("./splash-assets.cjs");
const splashState = require("./splash-state.cjs");

protocol.registerSchemesAsPrivileged([
  { scheme: "dpe-splash", privileges: { standard: true, secure: true, stream: true } },
]);

module.exports = function createWindow(onAction) {
  protocol.handle(
    "dpe-splash",
    assetHandler(
      path.join(process.env.DISCORD_USER_DATA_DIR, "dpe-updater/splash"),
      net.fetch,
      process.env.DPE_CLIENT_OFFLINE === "1",
    ),
  );
  const window = new BrowserWindow({
    title: "Discord",
    width: 300,
    height: 350,
    frame: false,
    resizable: false,
    center: true,
    backgroundColor: "#2f3136",
    show: false,
    autoHideMenuBar: true,
    icon: path.join(process.env.DPE_APP_ROOT, "discord.png"),
    webPreferences: {
      preload: path.join(process.env.DPE_APP_ROOT, "bootstrap/src/splash/preload.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
    },
  });
  const translate = splashState();
  let state = { status: "checking-for-updates" };
  const quit = (event) => {
    if (event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame)
      return;
    onAction("close");
  };
  ipcMain.on("sq", quit);
  window.once("closed", () => ipcMain.removeListener("sq", quit));
  window.webContents.once("did-finish-load", () => window.webContents.send("state", state));
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  window.once("ready-to-show", () => window.show());
  void window.loadURL("dpe-splash://app/index.html").catch((error) => {
    console.error(error);
    window.close();
  });
  return {
    window,
    update(value) {
      state = translate(value);
      if (!window.isDestroyed()) window.webContents.send("state", state);
    },
  };
};
