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
      save: () => {},
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
  const link = window.handlers.get("dpe-config-open-link");
  await assert.rejects(link(f.event(window), "https://untrusted.invalid"), /Unknown project link/);
  await assert.rejects(link({ sender: {}, senderFrame: {} }, "github"), /Invalid settings window/);
  await link(f.event(window), "github");
  assert.equal(f.links.length, 1);
  await link(f.event(window), "openh264-license");
  assert.equal(f.links.at(-1), "/app/integration/updater/OPENH264-BINARY-LICENSE.txt");
  f.ipcMain.emit("cr", f.event(window));
  assert.equal(window.messages.at(-1)[0], "dpe-setup-reminder");
  window.emit("closed");
  assert.equal(f.ipcMain.listenerCount("cs"), 0);
});
