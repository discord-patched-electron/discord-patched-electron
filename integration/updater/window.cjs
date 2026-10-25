const { BrowserWindow, ipcMain, net, protocol } = require("electron");
const path = require("node:path");
const assetHandler = require("./splash-assets.cjs");
const splashState = require("./splash-state.cjs");
const preferences = require("./profile.cjs");

protocol.registerSchemesAsPrivileged([
  {
    scheme: "dpe-splash",
    privileges: {
      standard: true,
      secure: true,
      stream: true,
      supportFetchAPI: true,
      corsEnabled: true,
    },
  },
]);

let registered = false;
function register() {
  if (registered) return;
  protocol.handle(
    "dpe-splash",
    assetHandler(
      path.join(process.env.DPE_PROFILE_DIR, "discord_asset_cache/openasar"),
      net.fetch,
      process.env.DPE_CLIENT_OFFLINE === "1",
    ),
  );
  registered = true;
}

module.exports = function createWindow(onAction) {
  register();
  const window = new BrowserWindow({
    title: "Discord",
    width: 300,
    height: 300,
    frame: false,
    resizable: false,
    center: true,
    backgroundColor: preferences.background(),
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
  let themeReady;
  window.webContents.once("dom-ready", () => {
    themeReady = preferences.theme(window);
  });
  window.webContents.once("did-finish-load", async () => {
    await themeReady;
    if (!window.isDestroyed()) window.webContents.send("state", state);
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  window.once("ready-to-show", () => {
    if (!process.argv.includes("--start-minimized")) window.show();
  });
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
module.exports.register = register;
