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
  const window = require("../desktop/preferences.cjs").createAuxiliaryWindow(BrowserWindow, {
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
      attributions:
        "https://github.com/discord-patched-electron/discord-patched-electron/blob/main/THIRD_PARTY.md",
    };
    if (!Object.hasOwn(links, name)) throw new Error("Unknown project link");
    await shell.openExternal(links[name]);
  });
  handle("dpe-export-logs", () => require("./export-logs.cjs").exportLogs(window));
  handle("dpe-config-diagnostic", (name) => require("./diagnostics.cjs").open(name));
  handle("dpe-screenshare-load", () => require("./screensharing.cjs").load(settings));
  handle("dpe-screenshare-save", (patch) => require("./screensharing.cjs").save(settings, patch));
  handle("dpe-screenshare-status", () => require("./screensharing.cjs").snapshot());
  handle("dpe-capture-permission-status", () => require("./capture-permission.cjs").status());
  handle("dpe-capture-permission-allow", () => {
    if (
      !require("../video/settings.cjs").normalize(settings.get("openasar", {}).screensharing)
        .experimentalCapture
    )
      throw new Error("Enable experimental capture methods first.");
    return require("./capture-permission.cjs").allow();
  });
  handle("dpe-capture-permission-revoke", () => require("./capture-permission.cjs").revoke());
  handle("dpe-capture-selection", () => require("./capture-backends.cjs").state());
  handle("dpe-capture-refresh", () => require("./capture-backends.cjs").refresh());
  handle("dpe-capture-choose", (id, source, authorize) =>
    require("./capture-backends.cjs").choose(id, source, authorize === true),
  );
  handle("dpe-capture-cancel", (id) => require("./capture-backends.cjs").cancel(id));
  const desktopState = async () => {
    const desktop = require("../desktop/preferences.cjs");
    return {
      ...desktop.status(),
      values: desktop.normalize(settings.get("openasar", {}).desktop),
      linksElsewhere: await desktop.linksElsewhere(),
    };
  };
  handle("dpe-desktop-load", desktopState);
  handle("dpe-desktop-claim-links", () => {
    require("../desktop/preferences.cjs").claimLinks();
    return desktopState();
  });
  handle("dpe-desktop-save", async (patch) => {
    const desktop = require("../desktop/preferences.cjs");
    if (
      !patch ||
      typeof patch !== "object" ||
      Array.isArray(patch) ||
      Object.entries(patch).some(
        ([key, value]) => !Object.hasOwn(desktop.DEFAULTS, key) || typeof value !== "boolean",
      )
    )
      throw new Error("Choose supported desktop settings.");
    const previous = settings.get("openasar", {});
    const next = { ...previous, desktop: desktop.normalize({ ...previous.desktop, ...patch }) };
    settings.set("openasar", next);
    try {
      settings.save();
    } catch (error) {
      settings.set("openasar", previous);
      throw error;
    }
    global.oaConfig = { ...next, autoupdate: false };
    const errors = [];
    try {
      await desktop.apply(next.desktop);
    } catch (error) {
      // The preference is already durable. Keep the UI on the saved values
      // when a window closes mid-update or a live native operation fails.
      console.error("Could not apply desktop preferences", error);
      errors.push("Settings saved. Restart Discord to finish applying these changes.");
    }
    if (patch.discordLinks && !previous.desktop?.discordLinks)
      try {
        desktop.claimLinks();
      } catch (error) {
        errors.push(error.message);
      }
    return { ...(await desktopState()), error: errors.join(" ") };
  });
  const spellingState = () => {
    const state = require("../desktop/spellcheck.cjs").status();
    const configured = settings.get("openasar", {}).spellcheckLanguages;
    return {
      ...state,
      useSystemLanguages: require("../desktop/spellcheck.cjs").usesSystemLanguages(
        settings.get("openasar", {}),
      ),
      configuredLanguages: Array.isArray(configured)
        ? configured.filter((language) => state.availableLanguages.includes(language))
        : [],
    };
  };
  handle("dpe-spellcheck-load", async (force) => {
    await require("../desktop/spellcheck.cjs").refresh(force === true);
    return spellingState();
  });
  handle("dpe-spellcheck-save", (value) => {
    const spelling = require("../desktop/spellcheck.cjs");
    const available = spelling.status().availableLanguages;
    if (
      !value ||
      typeof value.enabled !== "boolean" ||
      (value.useSystemLanguages !== undefined && typeof value.useSystemLanguages !== "boolean") ||
      !Array.isArray(value.languages) ||
      value.languages.length > available.length ||
      value.languages.some(
        (language) => typeof language !== "string" || !available.includes(language),
      )
    )
      throw new Error("Choose supported spelling languages.");
    const previous = settings.get("openasar", {});
    const useSystemLanguages = value.useSystemLanguages ?? !value.languages.length;
    const next = {
      ...previous,
      spellcheckEnabled: value.enabled && (useSystemLanguages || value.languages.length > 0),
      spellcheckLanguages: [...new Set(value.languages)],
      spellcheckUseSystemLanguages: useSystemLanguages,
    };
    settings.set("openasar", next);
    try {
      settings.save();
    } catch (error) {
      settings.set("openasar", previous);
      throw error;
    }
    global.oaConfig = { ...next, autoupdate: false };
    spelling.apply(next);
    return spellingState();
  });
  handle("dpe-spellcheck-uninstall", async (language) => {
    const spelling = require("../desktop/spellcheck.cjs");
    const previous = settings.get("openasar", {});
    if (spelling.usesSystemLanguages(previous))
      throw new Error("Turn off Use system languages before uninstalling a dictionary.");
    const aliases = spelling.aliases(language);
    const languages = (previous.spellcheckLanguages || []).filter(
      (code) => !aliases.includes(code),
    );
    const next = {
      ...previous,
      spellcheckLanguages: languages,
      spellcheckEnabled: previous.spellcheckEnabled !== false && languages.length > 0,
    };
    settings.set("openasar", next);
    try {
      settings.save();
    } catch (error) {
      settings.set("openasar", previous);
      throw error;
    }
    global.oaConfig = { ...next, autoupdate: false };
    const applied = spelling.apply(next);
    if (applied.error) throw new Error(applied.error);
    await spelling.uninstall(language);
    return spellingState();
  });
  handle("dpe-tray-load", () => require("./tray.cjs").status());
  handle("dpe-tray-preset", (preset) => require("./tray.cjs").setPreset(preset));
  handle("dpe-tray-state-preset", (state, preset) =>
    require("./tray.cjs").setStatePreset(state, preset),
  );
  handle("dpe-tray-choose", (state) => require("./tray.cjs").choose(window, state));
  handle("dpe-tray-reset", (state) => require("./tray.cjs").reset(state));

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
