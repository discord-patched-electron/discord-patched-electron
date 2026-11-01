// Local settings and Welcome share a restricted API, with independent windows.
const { app, BrowserWindow, ipcMain, screen, shell } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const preferences = require("./profile.cjs");
const windows = new Map();

function settingsStore() {
  if (global.settings) return global.settings;
  // Welcome intentionally runs before OpenAsar/Discord. Keep its configuration
  // available to the same settings API without evaluating either bootstrap.
  const changes = new Map();
  global.settings = {
    get: (key, fallback) =>
      changes.has(key) ? changes.get(key) : (preferences.settings()[key] ?? fallback),
    set: (key, value) => changes.set(key, value),
    save() {
      const filename = path.join(process.env.DPE_PROFILE_DIR, "settings.json");
      const temporary = filename + "." + process.pid + ".tmp";
      try {
        fs.writeFileSync(
          temporary,
          JSON.stringify({ ...preferences.settings(), ...Object.fromEntries(changes) }),
          { mode: 0o600 },
        );
        fs.renameSync(temporary, filename);
      } finally {
        fs.rmSync(temporary, { force: true });
      }
    },
  };
  return global.settings;
}

function open(tab = "settings", sourceContents, options = {}) {
  const mode = tab === "setup" ? "welcome" : "settings";
  const current = windows.get(mode);
  if (current && !current.isDestroyed()) {
    if (current.isVisible()) {
      current.show();
      current.focus();
    }
    current.webContents.send("dpe-config-tab", tab);
    return current;
  }
  require("./window.cjs").register();
  // Settings replaces Welcome and outlives it. Create that replacement without
  // a native parent instead of attaching and then detaching it during handoff.
  const parent = options.independent
    ? undefined
    : options.parent ||
      (sourceContents && BrowserWindow.fromWebContents(sourceContents)) ||
      BrowserWindow.getAllWindows().find((item) =>
        require("./presentation.cjs").trusted(item.webContents),
      );
  const area = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workAreaSize;
  const width = Math.min(mode === "welcome" ? 560 : 1100, Math.max(280, area.width - 48));
  const height = Math.min(mode === "welcome" ? 640 : 800, Math.max(320, area.height - 48));
  const initiallyFixed =
    mode === "settings" &&
    process.platform === "linux" &&
    Boolean(process.env.WAYLAND_DISPLAY) &&
    app.commandLine.getSwitchValue("ozone-platform") !== "x11";
  const window = new BrowserWindow({
    title: mode === "welcome" ? "Welcome to DPE" : "DPE Settings",
    width,
    height,
    type: "dialog",
    ...(parent && !parent.isDestroyed() ? { parent, modal: false } : {}),
    frame: false,
    show: false,
    autoHideMenuBar: true,
    resizable: mode !== "welcome" && !initiallyFixed,
    maximizable: mode !== "welcome",
    minWidth: Math.min(320, width),
    minHeight: Math.min(360, height),
    center: true,
    backgroundColor: "#121214",
    webPreferences: {
      preload: path.join(__dirname, "config-preload.cjs"),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  if (initiallyFixed) {
    // Electron does not send a Wayland transient parent for BrowserWindow.
    // Fixed initial constraints let compositors float Settings on its first map.
    // show/ready-to-show precede that map. Native focus restores the original
    // resize constraints only after activation, without moving a tiled window.
    const restoreResizing = () => {
      if (!window.isDestroyed()) window.setResizable(true);
    };
    window.once("focus", restoreResizing);
    window.once("closed", () => window.removeListener("focus", restoreResizing));
  }
  windows.set(mode, window);
  const settings = settingsStore();
  settings.set("openasar", { ...preferences.config(), setup: true });
  settings.save();
  const handlers = {
    "dpe-config-mode"(event) {
      event.returnValue = mode;
    },
    "dpe-config-onboarding"(event) {
      event.returnValue = mode === "settings" && options.onboarding === true;
    },
    cg(event) {
      event.returnValue = settings.get("openasar", {});
    },
    cs(_event, config) {
      if (!config || typeof config !== "object" || Array.isArray(config)) return;
      const current = settings.get("openasar", {});
      // General and Appearance hold a renderer snapshot. Only their own editable
      // values may be copied back, so stale snapshots cannot undo backend state.
      const next = { ...current };
      for (const key of [
        "audioAutoDetect",
        "audioSkipScreenPicker",
        "noTrack",
        "noTyping",
        "themeSync",
        "quickstart",
        "multiInstance",
      ])
        if (typeof config[key] === "boolean") next[key] = config[key];
      if (typeof config.css === "string") next.css = config.css;
      settings.set("openasar", next);
      settings.save();
      global.oaConfig = { ...next, autoupdate: false };
    },
    cr() {
      settings.save();
      if (require("./mod-settings.cjs").isOnboardingSettings(window)) {
        void require("./mod-settings.cjs")
          .finishSettings(window)
          .catch(() => {});
        return;
      }
      if (require("./mod-settings.cjs").isOnboarding()) {
        window.webContents.send(
          "dpe-setup-reminder",
          "Close Settings to finish setup and restart Discord.",
        );
        return;
      }
      require("./relaunch.cjs").restart(app);
    },
    of() {
      void shell.openPath(path.join(process.env.DPE_PROFILE_DIR, "settings.json"));
    },
  };
  const valid = (event) =>
    event.sender === window.webContents && event.senderFrame === window.webContents.mainFrame;
  const listeners = Object.entries(handlers).map(([name, handle]) => {
    const listener = (event, ...args) => {
      if (valid(event)) handle(event, ...args);
    };
    ipcMain.on(name, listener);
    return [name, listener];
  });
  const handle = (name, callback) =>
    window.webContents.ipc.handle(name, async (event, ...args) => {
      if (!valid(event)) throw new Error("Invalid settings window");
      return callback(...args);
    });
  handle("dpe-config-open-link", async (name) => {
    if (name === "openh264-license") {
      const error = await shell.openPath(path.join(__dirname, "OPENH264-BINARY-LICENSE.txt"));
      if (error) throw new Error(error);
      return;
    }
    const links = {
      github: "https://github.com/discord-patched-electron/discord-patched-electron",
      nvidia: "https://github.com/elFarto/nvidia-vaapi-driver",
      releases: "https://github.com/discord-patched-electron/discord-patched-electron/releases",
      issues: "https://github.com/discord-patched-electron/discord-patched-electron/issues",
    };
    if (!Object.hasOwn(links, name)) throw new Error("Unknown project link");
    await shell.openExternal(links[name]);
  });
  handle("dpe-config-diagnostic", (name) => require("./diagnostics.cjs").open(name));
  handle("dpe-setup-action", async (action) => {
    if (mode !== "welcome") throw new Error("Finish setup in the Welcome window.");
    try {
      return await require("./mod-settings.cjs").setupAction(action);
    } catch (error) {
      return { error: error.message };
    }
  });
  window.once("closed", () => {
    for (const [name, listener] of listeners) ipcMain.removeListener(name, listener);
    windows.delete(mode);
  });
  require("./presentation.cjs").attach(window, sourceContents, {
    mode,
    waitForReady: true,
    lockIcons: mode === "welcome",
  });
  if (mode === "settings") require("./launch-settings.cjs").attach(window);
  require("./mod-settings.cjs").attach(window);
  window.webContents.once("did-finish-load", () => window.webContents.send("dpe-config-tab", tab));
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  void window
    .loadURL(
      mode === "welcome"
        ? "dpe-splash://welcome/config.html?mode=welcome"
        : "dpe-splash://app/config.html",
    )
    .catch(console.error);
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
    open("settings", event.sender);
});
module.exports = {
  open,
  settingsOpen: () => Boolean(windows.get("settings") && !windows.get("settings").isDestroyed()),
};
