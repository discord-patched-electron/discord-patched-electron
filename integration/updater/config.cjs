// Keep the upstream settings HTML/preload; constrain IPC to this local window.
const { app, BrowserWindow, ipcMain, shell } = require("electron");
const path = require("node:path");
const preferences = require("./profile.cjs");
let window;

function open() {
  if (window && !window.isDestroyed()) return window.show();
  require("./window.cjs").register();
  window = new BrowserWindow({
    width: 500,
    height: 650,
    frame: false,
    resizable: false,
    center: true,
    backgroundColor: "#2f3136",
    webPreferences: {
      preload: path.join(process.env.DPE_APP_ROOT, "bootstrap/src/config/preload.js"),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  const settings = global.settings;
  const initial = { ...preferences.config(), setup: true };
  settings.set("openasar", initial);
  settings.save();
  const handlers = {
    cg(event) {
      event.returnValue = settings.get("openasar", {});
    },
    cs(_event, config) {
      // Bootstrap/channel self-replacement would remove the fork integration.
      if (!config || typeof config !== "object" || Array.isArray(config)) return;
      settings.set("openasar", config);
      settings.save();
      global.oaConfig = { ...config, autoupdate: false };
    },
    cr() {
      settings.save();
      app.relaunch();
      app.quit();
    },
    of() {
      void shell.openPath(path.join(process.env.DPE_PROFILE_DIR, "settings.json"));
    },
  };
  const listeners = Object.entries(handlers).map(([name, handle]) => {
    const listener = (event, ...args) => {
      if (
        event.sender === window?.webContents &&
        event.senderFrame === window.webContents.mainFrame
      )
        handle(event, ...args);
    };
    ipcMain.on(name, listener);
    return [name, listener];
  });
  window.once("closed", () => {
    for (const [name, listener] of listeners) ipcMain.removeListener(name, listener);
    window = null;
  });
  window.webContents.once("dom-ready", () => preferences.theme(window));
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  void window.loadURL("dpe-splash://app/config.html").catch(console.error);
}

ipcMain.on("DISCORD_UPDATED_QUOTES", (event, value) => {
  const address = event.sender.getURL();
  if (!URL.canParse(address)) return;
  const url = new URL(address);
  if (
    value === "o" &&
    url.protocol === "https:" &&
    ["discord.com", "canary.discord.com", "ptb.discord.com"].includes(url.hostname) &&
    event.senderFrame === event.sender.mainFrame
  )
    open();
});
module.exports = { open };
