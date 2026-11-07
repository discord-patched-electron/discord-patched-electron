const assert = require("node:assert/strict");
const { test } = require("node:test");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function fixture({ wayland = false, ozonePlatform = "", platform = "linux" } = {}) {
  const windows = [];
  const ipcMain = new EventEmitter();
  const links = [];
  const flagEditors = [];
  const spellingCalls = [];
  const desktopCalls = [];
  const trayCalls = [];
  let saveError;
  const spelling = {
    refresh: async () => {},
    usesSystemLanguages: require("../../integration/desktop/spellcheck.cjs").usesSystemLanguages,
    status: () => ({
      enabled: state.spellcheckEnabled !== false,
      languages: state.spellcheckLanguages?.length ? state.spellcheckLanguages : ["en-GB"],
      availableLanguages: ["en-GB", "fr"],
      downloads: [],
    }),
    apply: (config) => spellingCalls.push(config),
  };
  let state = {
    setup: true,
    setupComplete: true,
    onboardingPending: false,
    clientMod: "vencord",
    launchFlags: { version: 2 },
    quickstart: false,
  };
  const app = { commandLine: { getSwitchValue: () => ozonePlatform } };
  const global = {
    settings: {
      get: () => state,
      set: (_key, value) => {
        state = value;
      },
      save: () => {
        if (saveError) throw saveError;
      },
    },
  };
  class BrowserWindow extends EventEmitter {
    static getAllWindows() {
      return windows;
    }
    static fromWebContents(contents) {
      return windows.find((window) => window.webContents === contents);
    }
    constructor(options) {
      super();
      this.options = options;
      this.resizeChanges = [];
      this.handlers = new Map();
      this.messages = [];
      this.webContents = Object.assign(new EventEmitter(), {
        mainFrame: {},
        ipc: { handle: (name, handler) => this.handlers.set(name, handler) },
        send: (...args) => this.messages.push(args),
        setWindowOpenHandler() {},
      });
      windows.push(this);
    }
    isDestroyed() {
      return this.destroyed === true;
    }
    isVisible() {
      return false;
    }
    setResizable(value) {
      this.resizeChanges.push(value);
    }
    loadURL(url) {
      this.url = url;
      return Promise.resolve();
    }
  }
  const context = {
    require(name) {
      if (name === "electron")
        return {
          app,
          BrowserWindow,
          ipcMain,
          screen: {
            getCursorScreenPoint: () => ({}),
            getDisplayNearestPoint: () => ({ workAreaSize: { width: 1920, height: 1080 } }),
          },
          shell: {
            openExternal: async (url) => links.push(url),
            openPath: async (file) => {
              links.push(file);
              return "";
            },
          },
        };
      if (name === "node:fs") return {};
      if (name === "node:path") return path;
      if (name === "../desktop/spellcheck.cjs") return spelling;
      if (name === "../desktop/preferences.cjs") {
        const desktop = require("../../integration/desktop/preferences.cjs");
        return {
          ...desktop,
          createAuxiliaryWindow: (Window, options) => new Window(options),
          status: () => ({ restartRequired: false }),
          apply: (value) => desktopCalls.push(value),
        };
      }
      if (name === "./tray.cjs")
        return {
          status: () => ({ preset: "discord", icons: [{ id: "default", custom: false }] }),
          setPreset: (preset) => trayCalls.push(["preset", preset]),
          setStatePreset: (state, preset) => trayCalls.push(["state-preset", state, preset]),
          choose: (window, state) => {
            trayCalls.push([window, state]);
            return [];
          },
          reset: (state) => {
            trayCalls.push([state]);
            return [];
          },
        };
      if (name === "./profile.cjs") return { config: () => state };
      if (name === "./window.cjs") return { register() {} };
      if (name === "./presentation.cjs") return { attach() {}, trusted: () => false };
      if (name === "./launch-settings.cjs") return { attach: (window) => flagEditors.push(window) };
      if (name === "./mod-settings.cjs")
        return { attach() {}, isOnboarding: () => true, isOnboardingSettings: () => false };
      throw new Error("Unexpected dependency " + name);
    },
    __dirname: "/app/integration/updater",
    module: { exports: {} },
    global,
    console,
    URL,
    process: {
      platform,
      env: { DPE_PROFILE_DIR: "/profile", ...(wayland ? { WAYLAND_DISPLAY: "wayland-1" } : {}) },
    },
  };
  vm.runInNewContext(fs.readFileSync("integration/updater/config.cjs", "utf8"), context);
  const event = (window) => ({
    sender: window.webContents,
    senderFrame: window.webContents.mainFrame,
  });
  return {
    api: context.module.exports,
    windows,
    ipcMain,
    links,
    flagEditors,
    spellingCalls,
    desktopCalls,
    trayCalls,
    failSave: () => {
      saveError = new Error("disk full");
    },
    event,
    get state() {
      return state;
    },
  };
}

test("Welcome and Settings are independent, ready-gated windows with real parent geometry", () => {
  const f = fixture();
  const welcome = f.api.open("setup");
  const settings = f.api.open("settings", undefined, { parent: welcome, onboarding: true });
  assert.deepEqual(f.flagEditors, [settings]);
  assert.equal(welcome.options.show, false);
  assert.equal(settings.options.show, false);
  assert.equal(welcome.options.resizable, false);
  assert.equal(welcome.options.maximizable, false);
  assert.equal(welcome.options.width, 560);
  assert.equal(welcome.options.height, 640);
  assert.equal(settings.options.resizable, true);
  assert.equal(settings.options.parent, welcome);
  assert.equal(settings.options.width, 1100);
  assert.equal(settings.options.height, 800);
  assert.equal(f.api.open("flags"), settings);
  const mode = f.event(welcome);
  f.ipcMain.emit("dpe-config-mode", mode);
  assert.equal(mode.returnValue, "welcome");
  const otherMode = f.event(settings);
  f.ipcMain.emit("dpe-config-mode", otherMode);
  assert.equal(otherMode.returnValue, "settings");
  const onboarding = f.event(settings);
  f.ipcMain.emit("dpe-config-onboarding", onboarding);
  assert.equal(onboarding.returnValue, true);
  const notSettings = f.event(welcome);
  f.ipcMain.emit("dpe-config-onboarding", notSettings);
  assert.equal(notSettings.returnValue, false);
});

test("replacement Settings never inherits explicit or source-window native ownership", () => {
  const f = fixture();
  const welcome = f.api.open("setup");
  const settings = f.api.open("settings", welcome.webContents, {
    parent: welcome,
    onboarding: true,
    independent: true,
  });
  assert.equal(settings.options.parent, undefined);
  assert.equal(settings.options.type, "dialog");
  assert.equal(settings.options.resizable, true);
  assert.equal(settings.options.width, 1100);
  assert.equal(settings.options.height, 800);
  const event = f.event(settings);
  f.ipcMain.emit("dpe-config-onboarding", event);
  assert.equal(event.returnValue, true);
});

test("Wayland Settings keeps initial floating constraints until native focus", () => {
  const f = fixture({ wayland: true });
  const welcome = f.api.open("setup");
  const settings = f.api.open("settings", welcome.webContents, {
    parent: welcome,
    onboarding: true,
    independent: true,
  });
  assert.equal(settings.options.resizable, false);
  assert.equal(settings.options.maximizable, true);
  assert.equal(settings.options.parent, undefined);
  assert.equal(settings.options.minWidth, 320);
  assert.equal(settings.options.minHeight, 360);
  settings.emit("ready-to-show");
  settings.emit("show");
  assert.deepEqual(settings.resizeChanges, []);
  settings.emit("focus");
  assert.deepEqual(settings.resizeChanges, [true]);
  settings.emit("focus");
  assert.deepEqual(settings.resizeChanges, [true]);
  welcome.emit("focus");
  assert.deepEqual(welcome.resizeChanges, []);
});

test("closing Wayland Settings before focus removes its deferred resize handler", () => {
  const f = fixture({ wayland: true });
  const settings = f.api.open("settings");
  assert.equal(settings.listenerCount("focus"), 1);
  settings.destroyed = true;
  settings.emit("closed");
  assert.equal(settings.listenerCount("focus"), 0);
  settings.emit("focus");
  assert.deepEqual(settings.resizeChanges, []);
});

test("X11 and other platforms retain normal Settings resize hints", () => {
  for (const options of [
    { wayland: true, ozonePlatform: "x11" },
    { wayland: false },
    { wayland: true, platform: "darwin" },
  ]) {
    const f = fixture(options);
    const settings = f.api.open("settings");
    assert.equal(settings.options.resizable, true);
    assert.equal(settings.listenerCount("focus"), 0);
  }
});

test("cached editable config cannot overwrite backend state or escape window IPC", async () => {
  const f = fixture();
  const window = f.api.open("settings");
  const stale = {
    setup: false,
    setupComplete: false,
    onboardingPending: true,
    clientMod: "none",
    launchFlags: {},
    quickstart: true,
    css: "body{}",
  };
  f.ipcMain.emit("cs", { sender: window.webContents, senderFrame: {} }, stale);
  assert.equal(f.state.quickstart, false);
  f.ipcMain.emit("cs", f.event(window), stale);
  assert.equal(f.state.quickstart, true);
  assert.equal(f.state.css, "body{}");
  assert.equal(f.state.setup, true);
  assert.equal(f.state.setupComplete, true);
  assert.equal(f.state.onboardingPending, false);
  assert.equal(f.state.clientMod, "vencord");
  assert.equal(f.state.launchFlags.version, 2);
  const exportLogs = window.handlers.get("dpe-export-logs");
  await assert.rejects(exportLogs({ sender: {}, senderFrame: {} }), /Invalid settings window/);
  await assert.rejects(
    exportLogs({ sender: window.webContents, senderFrame: {} }),
    /Invalid settings window/,
  );
  const link = window.handlers.get("dpe-config-open-link");
  await assert.rejects(link(f.event(window), "https://untrusted.invalid"), /Unknown project link/);
  await assert.rejects(link({ sender: {}, senderFrame: {} }, "github"), /Invalid settings window/);
  await link(f.event(window), "github");
  assert.equal(f.links.length, 1);
  await link(f.event(window), "attributions");
  assert.equal(
    f.links.at(-1),
    "https://github.com/discord-patched-electron/discord-patched-electron/blob/main/THIRD_PARTY.md",
  );
  await link(f.event(window), "openh264-license");
  assert.equal(f.links.at(-1), "/app/integration/updater/OPENH264-BINARY-LICENSE.txt");
  f.ipcMain.emit("cr", f.event(window));
  assert.equal(window.messages.at(-1)[0], "dpe-setup-reminder");
  window.emit("closed");
  assert.equal(f.ipcMain.listenerCount("cs"), 0);
});

test("spelling IPC validates languages and preserves unrelated settings and automatic selection", async () => {
  const f = fixture();
  const window = f.api.open("settings");
  const load = window.handlers.get("dpe-spellcheck-load");
  const save = window.handlers.get("dpe-spellcheck-save");
  assert.equal((await load(f.event(window))).configuredLanguages.length, 0);
  await assert.rejects(
    save({ sender: window.webContents, senderFrame: {} }, { enabled: true, languages: ["fr"] }),
    /Invalid settings/,
  );
  for (const value of [
    null,
    { enabled: 1, languages: [] },
    { enabled: true, languages: "fr" },
    { enabled: true, languages: ["unknown"] },
    { enabled: true, languages: [], useSystemLanguages: "yes" },
  ])
    await assert.rejects(save(f.event(window), value), /supported spelling/);
  const result = await save(f.event(window), {
    enabled: true,
    languages: ["en-GB", "fr"],
    clientMod: "none",
  });
  assert.deepEqual(Array.from(result.configuredLanguages), ["en-GB", "fr"]);
  assert.equal(f.state.clientMod, "vencord");
  assert.equal(f.spellingCalls.length, 1);
  f.ipcMain.emit("cs", f.event(window), { quickstart: true, spellcheckLanguages: [] });
  assert.deepEqual(
    Array.from(f.state.spellcheckLanguages),
    ["en-GB", "fr"],
    "stale General snapshot cannot overwrite spelling",
  );
  await save(f.event(window), { enabled: false, languages: [] });
  assert.equal(f.state.spellcheckEnabled, false);
  assert.equal(f.state.spellcheckLanguages.length, 0);
  f.failSave();
  await assert.rejects(save(f.event(window), { enabled: true, languages: ["fr"] }), /disk full/);
  assert.equal(f.state.spellcheckEnabled, false);
  assert.equal(f.spellingCalls.length, 2, "failed persistence must not apply runtime changes");
});

test("empty manual spelling selection disables checking without switching to system languages", async () => {
  const f = fixture();
  const window = f.api.open("settings");
  const save = window.handlers.get("dpe-spellcheck-save");
  const result = await save(f.event(window), {
    enabled: true,
    languages: [],
    useSystemLanguages: false,
  });
  assert.equal(result.enabled, false);
  assert.equal(result.useSystemLanguages, false);
  assert.equal(result.configuredLanguages.length, 0);
  assert.equal(f.state.spellcheckEnabled, false);
});

test("tray operations stay in the owning settings window", async () => {
  const f = fixture();
  const window = f.api.open("theming");
  const choose = window.handlers.get("dpe-tray-choose");
  await assert.rejects(choose({ sender: {}, senderFrame: {} }, "default"), /Invalid settings/);
  await choose(f.event(window), "speaking");
  assert.deepEqual(f.trayCalls, [[window, "speaking"]]);
  await window.handlers.get("dpe-tray-reset")(f.event(window), "default");
  assert.deepEqual(f.trayCalls[1], ["default"]);
  assert.equal(
    (await window.handlers.get("dpe-tray-load")(f.event(window))).icons[0].id,
    "default",
  );
  const preset = window.handlers.get("dpe-tray-preset");
  await assert.rejects(preset({ sender: {}, senderFrame: {} }, "vencord"), /Invalid settings/);
  await preset(f.event(window), "vencord");
  assert.deepEqual(f.trayCalls[2], ["preset", "vencord"]);
  const statePreset = window.handlers.get("dpe-tray-state-preset");
  await assert.rejects(
    statePreset({ sender: {}, senderFrame: {} }, "unread", "equicord"),
    /Invalid settings/,
  );
  await statePreset(f.event(window), "unread", "equicord");
  assert.deepEqual(f.trayCalls[3], ["state-preset", "unread", "equicord"]);
});

test("desktop IPC validates patches, preserves unrelated preferences and rolls back failed writes", async () => {
  const f = fixture();
  const window = f.api.open();
  const event = f.event(window);
  const load = window.handlers.get("dpe-desktop-load");
  const save = window.handlers.get("dpe-desktop-save");
  assert.equal((await load(event)).values.nativeTitlebar, false);
  for (const invalid of [
    null,
    [],
    { unknown: true },
    { tray: "no" },
    { webRtcIPHandlingPolicy: "anything" },
    { webRtcIPHandlingPolicy: "default" },
  ])
    await assert.rejects(save(event, invalid), /supported desktop/);
  await assert.rejects(save({ sender: {}, senderFrame: {} }, { tray: false }), /Invalid settings/);
  await save(event, { minimizeButton: false });
  await save(event, { nativeTitlebar: true });
  assert.equal(f.state.desktop.minimizeButton, false);
  assert.equal(f.state.desktop.nativeTitlebar, true);
  assert.equal(f.state.clientMod, "vencord");
  f.ipcMain.emit("cs", event, { quickstart: true, desktop: { tray: false } });
  assert.equal(
    f.state.desktop.tray,
    true,
    "stale General snapshots cannot overwrite desktop preferences",
  );
  await save(event, { startMinimized: true });
  assert.equal(f.state.desktop.startMinimized, true);
  const previous = f.state.desktop;
  f.failSave();
  await assert.rejects(save(event, { tray: false }), /disk full/);
  assert.equal(f.state.desktop, previous);
  assert.equal(f.desktopCalls.length, 3, "failed persistence never changes the live desktop");
});
