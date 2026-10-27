// Extend the pinned settings page; constrain IPC to this local window.
const { app, BrowserWindow, ipcMain, shell } = require("electron");
const path = require("node:path");
const preferences = require("./profile.cjs");
let window;

function open(tab = "settings") {
  if (window && !window.isDestroyed()) {
    window.show();
    window.focus();
    window.webContents.send("dpe-config-tab", tab);
    return window;
  }
  require("./window.cjs").register();
  window = new BrowserWindow({
    width: 560,
    height: 760,
    frame: false,
    resizable: false,
    minWidth: 320,
    minHeight: 360,
    center: true,
    backgroundColor: "#2f3136",
    webPreferences: {
      preload: path.join(__dirname, "config-preload.cjs"),
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
      // The launch editor saves independently; the older page holds a snapshot.
      // Preserve its latest launch settings when another OpenAsar option changes.
      const current = settings.get("openasar", {});
      config = { ...config };
      for (const key of ["launchFlags", "cmdPreset", "customFlags"]) {
        delete config[key];
        if (Object.hasOwn(current, key)) config[key] = current[key];
      }
      settings.set("openasar", config);
      settings.save();
      global.oaConfig = { ...config, autoupdate: false };
    },
    cr() {
      settings.save();
      require("./relaunch.cjs").restart(app);
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
  ipcMain.handle("dpe-config-open-link", async (event, name) => {
    if (event.sender !== window?.webContents || event.senderFrame !== window.webContents.mainFrame)
      throw new Error("Invalid settings window");
    const links = {
      github: "https://github.com/discord-patched-electron/discord-patched-electron",
      nvidia: "https://github.com/elFarto/nvidia-vaapi-driver",
    };
    if (!Object.hasOwn(links, name)) throw new Error("Unknown project link");
    await shell.openExternal(links[name]);
  });
  window.once("closed", () => {
    ipcMain.removeHandler("dpe-config-open-link");
    for (const [name, listener] of listeners) ipcMain.removeListener(name, listener);
    window = null;
  });
  window.webContents.once("dom-ready", () => preferences.theme(window));
  require("./launch-settings.cjs").attach(window);
  window.webContents.once("did-finish-load", () => window.webContents.send("dpe-config-tab", tab));
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  void window.loadURL("dpe-splash://app/config.html").catch(console.error);
  return window;
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
