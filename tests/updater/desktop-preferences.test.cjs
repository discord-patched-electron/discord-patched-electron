const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { test } = require("node:test");
const {
  DEFAULTS,
  normalize,
  titlebarCSS,
  webcamCSS,
  createController,
  configureMediaControls,
} = require("../../integration/desktop/preferences.cjs");
const { patch } = require("../../integration/desktop/preferences-patch.cjs");

class Contents extends EventEmitter {
  constructor() {
    super();
    this.styles = new Map();
    this.url = "https://canary.discord.com/channels/@me";
    this.sequence = 0;
  }
  isDestroyed() {
    return false;
  }
  getURL() {
    return this.url;
  }
  async insertCSS(css) {
    const key = String(++this.sequence);
    this.styles.set(key, css);
    return key;
  }
  async removeInsertedCSS(key) {
    this.styles.delete(key);
  }
}
class Window extends EventEmitter {
  constructor(options) {
    super();
    this.options = options;
    this.webContents = new Contents();
    this.visible = options.show !== false;
    this.minimum = [options.minWidth, options.minHeight];
    this.size = [options.width || 1280, options.height || 720];
    this.nativeControls = { minimize: true, maximize: true, close: true };
  }
  isDestroyed() {
    return false;
  }
  isFullScreen() {
    return false;
  }
  isVisible() {
    return this.visible;
  }
  setMinimumSize(...value) {
    this.minimum = value;
  }
  getSize() {
    return this.size;
  }
  setSize(...value) {
    this.size = value;
  }
  flashFrame(value) {
    this.flash = value;
  }
  showInactive() {
    this.visible = true;
  }
  minimize() {
    this.minimized = true;
  }
  setSkipTaskbar(value) {
    this.skipTaskbar = value;
  }
  getBounds() {
    return { x: 0, y: 0, width: this.size[0], height: this.size[1] };
  }
  getWindowControls() {
    return { ...this.nativeControls };
  }
  setWindowControls(value) {
    this.nativeControls = { ...value };
  }
}
function fixture(value = {}, options) {
  const app = Object.assign(new EventEmitter(), {
    isReady: () => true,
    setBadgeCount: (value) => {
      app.badge = value;
    },
  });
  const controller = createController(
    {
      app,
      screen: {
        getAllDisplays: () => [
          { workArea: { x: 0, y: 0, width: 1280, height: 720 }, scaleFactor: 1 },
        ],
        getDisplayMatching: () => ({
          workArea: { x: 0, y: 0, width: 1280, height: 720 },
          scaleFactor: 1,
        }),
      },
    },
    () => value,
    options,
  );
  const create = () =>
    controller.createMainWindow(Window, {
      minWidth: 940,
      minHeight: 500,
      frame: false,
      width: 1280,
      height: 720,
    });
  return { app, controller, create };
}
const settled = () => new Promise((resolve) => setImmediate(resolve));

test("webcam flip changes capture while the preview always keeps the same orientation", async () => {
  assert.equal(normalize().webcamFlip, false);
  assert.equal(normalize({ webcamFlip: "true" }).webcamFlip, false);
  assert.match(webcamCSS(), /transform:none/);
  const { controller, create } = fixture();
  const window = create();
  const messages = [];
  window.webContents.send = (...args) => messages.push(args);
  await controller.apply({ webcamFlip: true });
  assert.deepEqual(messages.at(-1), ["dpe-camera-flip", true]);
  await controller.cameraStatus(window.webContents, { supported: true, active: true });
  assert.deepEqual([...window.webContents.styles.values()], [webcamCSS()]);
  await controller.cameraStatus({}, { supported: false, active: false });
  assert.equal(controller.status().error, undefined, "unobserved senders cannot change state");
  await controller.cameraStatus(window.webContents, { supported: false, active: false });
  assert.match(controller.status().error, /unavailable/);
  await controller.apply({ webcamFlip: false });
  assert.deepEqual(messages.at(-1), ["dpe-camera-flip", false]);
  assert.equal(controller.status().error, undefined);
  assert.deepEqual([...window.webContents.styles.values()], [webcamCSS()]);
  assert.equal(controller.status().restartRequired, false);
});

test("desktop settings normalize only supported values and preserve saved button preferences", async () => {
  assert.deepEqual(normalize(), DEFAULTS);
  assert.deepEqual(normalize(null), DEFAULTS);
  assert.deepEqual(
    normalize({ nativeTitlebar: "yes", tray: 0, webRtcIPHandlingPolicy: "unsafe", extra: true }),
    DEFAULTS,
  );
  const value = normalize({ nativeTitlebar: true, minimizeButton: false, maximizeButton: true });
  assert.equal(value.minimizeButton, false);
  assert.equal(value.maximizeButton, true);
  assert.equal(value.windowControls, true);
  assert.equal(Object.hasOwn(value, "webRtcIPHandlingPolicy"), false);
});

test("native titlebars use native frame options and report restart only for an existing mismatched window", async () => {
  const { controller, create } = fixture();
  assert.equal((await controller.apply({ nativeTitlebar: true })).restartRequired, false);
  const window = create();
  assert.equal(window.options.frame, true);
  assert.equal(window.options.titleBarStyle, "default");
  assert.equal(controller.status().restartRequired, false);
  assert.equal((await controller.apply({ nativeTitlebar: false })).restartRequired, true);
  window.emit("closed");
  assert.equal(controller.status().restartRequired, false);
  const overlay = { outOfProcessOverlay: true, frame: false };
  assert.equal(
    controller.windowOptions(overlay),
    overlay,
    "native overlay surfaces are not reframed",
  );
});

test("minimum size toggles live and Discord IPC cannot restore restrictions while disabled", async () => {
  const { controller, create } = fixture();
  const window = create();
  await controller.apply({ minimumWindowSize: false });
  assert.deepEqual(window.minimum, [1, 1]);
  controller.setMinimumSize(window, 1000, 600);
  assert.deepEqual(window.minimum, [1, 1]);
  window.size = [300, 200];
  await controller.apply({ minimumWindowSize: true });
  assert.deepEqual(window.minimum, [1000, 600]);
  assert.deepEqual(window.size, [1000, 600]);
  assert.equal(controller.minimum(500), 500);
});

test("disabling the tray restores hidden windows to the taskbar and disables close-to-tray", async () => {
  const { controller, create } = fixture();
  const window = create();
  window.visible = false;
  const calls = [];
  controller.registerTray(
    () => calls.push("create"),
    () => calls.push("destroy"),
  );
  await controller.apply({ tray: false });
  assert.deepEqual(calls, ["destroy"]);
  assert.equal(window.visible, true);
  assert.equal(window.minimized, true);
  assert.equal(window.skipTaskbar, false);
  assert.equal(controller.minimizeToTray(true), false);
  await controller.apply({ tray: true });
  assert.deepEqual(calls, ["destroy", "create"]);
  assert.equal(controller.minimizeToTray(true), true);
  assert.equal(controller.minimizeWithoutTray(window), false);
});

test("start minimized honors stored preference and explicit launcher flag", async () => {
  assert.equal(fixture({}, { argv: [] }).controller.startMinimized(), false);
  assert.equal(fixture({ startMinimized: true }, { argv: [] }).controller.startMinimized(), true);
  assert.equal(fixture({}, { argv: ["--start-minimized"] }).controller.startMinimized(), true);
});

test("notification settings filter native flashes and badges without losing the unfiltered count", async () => {
  const { app, controller, create } = fixture();
  const window = create();
  controller.setBadgeCount(app, 4);
  controller.flashFrame(window, true);
  assert.equal(app.badge, 4);
  assert.equal(window.flash, true);
  await controller.apply({ notificationFlash: false, appBadge: false });
  assert.equal(app.badge, 0);
  assert.equal(window.flash, false);
  controller.flashFrame(window, true);
  assert.equal(window.flash, false);
  await controller.apply({ badgeOnlyMentions: true });
  assert.equal(app.badge, 4);
  controller.setBadgeCount(app, -1);
  assert.equal(app.badge, 0);
  await controller.apply({ badgeOnlyMentions: false });
  assert.equal(app.badge, -1);
  const dock = {
    setBadge: (value) => {
      dock.value = value;
    },
  };
  controller.setDockBadge(dock, "•");
  await controller.apply({ badgeOnlyMentions: true });
  assert.equal(dock.value, "");
  controller.setDockBadge(dock, "12");
  assert.equal(dock.value, "12");
});

test("auxiliary windows preserve their constraints and publish effective custom control preferences", async () => {
  const { controller, create } = fixture();
  const window = create();
  const updates = [];
  const unsubscribe = controller.subscribeControls(window, (value) => updates.push(value));
  assert.equal(updates.length, 1);
  await controller.apply({ minimizeButton: false });
  assert.equal(updates.at(-1).minimizeButton, false);
  assert.equal(updates.at(-1).closeButton, true);
  await controller.apply({ minimizeButton: false, tray: false });
  assert.equal(updates.length, 2, "unrelated settings do not republish controls");
  await controller.apply({ nativeTitlebar: true });
  const auxiliary = controller.createAuxiliaryWindow(Window, {
    minWidth: 280,
    minHeight: 320,
    frame: false,
  });
  assert.equal(auxiliary.options.frame, true);
  assert.deepEqual(auxiliary.minimum, [280, 320]);
  await controller.apply({ nativeTitlebar: false });
  assert.equal(controller.effectiveControls(auxiliary).windowControls, false);
  assert.equal(controller.effectiveControls(window).windowControls, true);
  unsubscribe();
  const before = updates.length;
  await controller.apply({ windowControls: false });
  assert.equal(updates.length, before);
  const closeUpdates = [];
  controller.subscribeControls(auxiliary, (value) => closeUpdates.push(value));
  auxiliary.emit("closed");
  await controller.apply({ windowControls: true });
  assert.equal(closeUpdates.length, 1, "closed subscribers are removed");
});

test("window controls use only their existing class tokens and stylesheet swaps converge on the newest setting", async () => {
  assert.equal(titlebarCSS(DEFAULTS), "");
  assert.match(titlebarCSS({ minimizeButton: false }), /nth-child\(1 of/);
  assert.match(titlebarCSS({ maximizeButton: false }), /nth-child\(2 of/);
  assert.match(titlebarCSS({ closeButton: false }), /nth-child\(3 of/);
  assert.match(titlebarCSS({ windowControls: false }), /display:none/);
  assert.match(titlebarCSS({ closeButton: false }), /display:none/);
  assert.doesNotMatch(titlebarCSS({ closeButton: false }), /margin:|padding/);
  assert.match(titlebarCSS({ windowControls: false }), /padding-inline-end:var\(--space-12,12px\)/);
  assert.equal(
    titlebarCSS({ minimizeButton: false, maximizeButton: false, closeButton: false }),
    titlebarCSS({ windowControls: false }),
  );
  assert.doesNotMatch(titlebarCSS({ nativeTitlebar: true }), /topBar|toolbar|body|window-chrome/);
  const { controller, create } = fixture();
  const window = create();
  await Promise.all([
    controller.apply({ windowControls: false }),
    controller.apply({ closeButton: false }),
  ]);
  assert.deepEqual(
    [...window.webContents.styles.values()],
    [titlebarCSS({ closeButton: false }) + webcamCSS()],
  );
  await controller.apply({ windowControls: true });
  await settled();
  assert.deepEqual([...window.webContents.styles.values()], [webcamCSS()]);
  window.webContents.url = "https://example.org/";
  await controller.apply({ windowControls: false });
  await settled();
  assert.deepEqual(
    [...window.webContents.styles.values()],
    [titlebarCSS({ windowControls: false }) + webcamCSS()],
  );
});

test("third-party windows are observed without duplicate handlers or inherited Discord size constraints", async () => {
  const { controller } = fixture();
  const window = new Window({ width: 500, height: 400, minWidth: 120, minHeight: 100 });
  window.webContents.url = "data:text/html,Third-party%20window";
  controller.observeWindow(window);
  controller.observeWindow(window);
  assert.equal(window.webContents.listenerCount("dom-ready"), 1);
  await controller.apply({ closeButton: false });
  assert.deepEqual(window.nativeControls, { minimize: true, maximize: true, close: false });
  await controller.apply({ windowControls: false });
  assert.deepEqual(window.nativeControls, { minimize: false, maximize: false, close: false });
  await controller.apply({ closeButton: false, nativeTitlebar: true });
  assert.deepEqual(window.nativeControls, { minimize: true, maximize: true, close: true });
  assert.deepEqual(window.minimum, [120, 100]);
  assert.equal(
    controller.status().restartRequired,
    false,
    "unmanaged native frames need no reframe",
  );
  assert.deepEqual(
    [...window.webContents.styles.values()],
    [titlebarCSS({ nativeTitlebar: true }) + webcamCSS()],
  );
  controller.attachPopout(window, { outOfProcessOverlay: true });
  window.webContents.emit("dom-ready");
  await settled();
  assert.equal(window.webContents.styles.size, 0, "overlay rendering stays untouched");
});

test("genuine stylesheet failures are reported while closed windows do not fail settings saves", async () => {
  const { controller, create } = fixture();
  const window = create();
  await settled();
  window.webContents.insertCSS = async () => {
    throw Error("Renderer unavailable");
  };
  await assert.rejects(controller.apply({ closeButton: false }), /Renderer unavailable/);
  window.isDestroyed = () => true;
  await controller.apply({ closeButton: false });
});

test("desktop integration patch validates each native call site exactly once", async () => {
  const sites = [];
  patch("", "/preferences.cjs", (source, before, after) => {
    sites.push([before, after]);
    return source;
  });
  const original = require("./desktop-preferences-fixture.cjs");
  const replace = (source, before, after) => {
    if (source.split(before).length !== 2) throw Error("Unsupported Discord client layout");
    return source.replace(before, after);
  };
  const transformed = patch(original, "/preferences.cjs", replace);
  for (const [, after] of sites) assert.ok(transformed.includes(after));
  const locked = original.replace(
    "exports.setupPopout=function(popoutWindow,key,options,webappEndpoint,openerWebContents){",
    "exports.setupPopout=function(popoutWindow,key,options,webappEndpoint){",
  );
  assert.match(
    patch(locked, "/preferences.cjs", replace),
    /exports\.setupPopout=function\(popoutWindow,key,options,webappEndpoint\)\{require\("\/preferences\.cjs"\)\.attachPopout\(popoutWindow,options\);/,
  );
  const spellcheck = original.replace(
    "webappEndpoint,openerWebContents){",
    "webappEndpoint,openerWebContents,onSpellcheckResult){",
  );
  const modern = patch(spellcheck, "/preferences.cjs", replace);
  assert.ok(
    modern.includes(
      'openerWebContents,onSpellcheckResult){require("/preferences.cjs").attachPopout(popoutWindow,options);',
    ),
  );
  assert.throws(() => patch(original + locked, "/preferences.cjs", replace), /Ambiguous/);
  assert.throws(() => patch(original + original, "/preferences.cjs", replace), /Unsupported/);
  assert.throws(() => patch("unexpected layout", "/preferences.cjs", replace), /Unsupported/);
});

test("video popout startup hints survive disabled minimum sizes and restore real minima after mapping", async () => {
  for (const enabled of [true, false]) {
    const { controller } = fixture({ minimumWindowSize: enabled });
    const options = {
      width: 854,
      height: 480,
      minWidth: 854,
      dpeVideo: true,
      dpeFloating: true,
      minHeight: 480,
      dpeMinimum: [320, 180],
      dpeMaximum: [0, 0],
      dpeResizable: true,
    };
    const window = new Window(options);
    window.windowKey = "DISCORD_CHANNEL_CALL_POPOUT";
    window.setResizable = (value) => {
      window.resizable = value;
    };
    window.setMaximumSize = (...value) => {
      window.maximum = value;
    };
    controller.attachPopout(window, options);
    window.emit("ready-to-show");
    assert.deepEqual(window.minimum, [854, 480]);
    window.emit("focus");
    assert.equal(window.resizable, true);
    assert.deepEqual(window.maximum, [0, 0]);
    assert.deepEqual(window.minimum, enabled ? [320, 180] : [1, 1]);
    window.setSize(640, 360);
    await controller.apply({ minimumWindowSize: true });
    assert.deepEqual(window.minimum, [320, 180]);
    assert.deepEqual(window.size, [640, 360]);
    window.emit("focus");
    assert.deepEqual(window.size, [640, 360]);
  }
});

test("popout titlebar follows its floating preference and global controls always win", async () => {
  const { controller } = fixture();
  const window = new Window({ width: 512, height: 288 });
  window.windowKey = "DISCORD_CHANNEL_CALL_POPOUT";
  controller.attachPopout(window, {
    dpeVideo: true,
    dpeFloating: true,
    minWidth: 320,
    minHeight: 180,
  });
  assert.equal(controller.effectiveControls(window).windowControls, false);
  await controller.apply({ popoutHideTitlebar: false });
  assert.equal(controller.effectiveControls(window).windowControls, true);
  await controller.apply({ popoutHideTitlebar: false, windowControls: false });
  assert.equal(controller.effectiveControls(window).windowControls, false);
  assert.match([...window.webContents.styles.values()][0], /wordmark_/);
  const tiled = new Window({ width: 512, height: 288 });
  tiled.windowKey = "DISCORD_CHANNEL_CALL_POPOUT";
  controller.attachPopout(tiled, {
    dpeVideo: true,
    dpeFloating: false,
    minWidth: 320,
    minHeight: 180,
  });
  await controller.apply({});
  assert.equal(controller.effectiveControls(tiled).windowControls, true);
});

test("desktop media controls default off, preserve flags and allow opting in", async () => {
  assert.equal(normalize().mediaControls, false);
  assert.equal(normalize({ mediaControls: "true" }).mediaControls, false);
  const switches = {
    "enable-features": "Other:param/value,MediaSessionService,HardwareMediaKeyHandling<Trial",
    "disable-features": "Unrelated,MediaSessionService",
  };
  const commandLine = {
    getSwitchValue: (name) => switches[name] || "",
    appendSwitch: (name, value) => {
      switches[name] = value;
    },
    removeSwitch: (name) => {
      delete switches[name];
    },
  };
  configureMediaControls(commandLine);
  assert.equal(switches["enable-features"], "Other:param/value");
  assert.equal(
    switches["disable-features"],
    "Unrelated,MediaSessionService,HardwareMediaKeyHandling",
  );
  configureMediaControls(commandLine);
  assert.equal(
    switches["disable-features"],
    "Unrelated,MediaSessionService,HardwareMediaKeyHandling",
  );
  const before = { ...switches };
  configureMediaControls(commandLine, { mediaControls: true });
  assert.deepEqual(switches, before, "opting in leaves explicit launch flags untouched");
  const { controller } = fixture();
  assert.equal(controller.status().restartRequired, false);
  await controller.apply({ mediaControls: true });
  assert.equal(controller.status().restartRequired, true);
  await controller.apply({ mediaControls: false });
  assert.equal(controller.status().restartRequired, false);
  const enabled = fixture({ mediaControls: true }).controller;
  await enabled.apply({ mediaControls: false });
  assert.equal(enabled.status().restartRequired, true);
});

test("PiP hides native frames independently and exposes only owned trusted corner actions", async (t) => {
  const { controller } = fixture();
  await controller.apply({ nativeTitlebar: true, popoutHideTitlebar: true });
  const options = controller.popoutOptions(
    { frame: false, width: 640, height: 360, minWidth: 320, minHeight: 180 },
    "DISCORD_CHANNEL_CALL_POPOUT",
    { isDestroyed: () => false, getBounds: () => ({ x: 0, y: 0, width: 900, height: 600 }) },
  );
  assert.equal(options.frame, false);
  assert.equal(options.alwaysOnTop, true);
  const window = new Window({ width: 512, height: 288 });
  window.windowKey = "DISCORD_CHANNEL_CALL_POPOUT";
  controller.attachPopout(window, { ...options, dpeVideo: false });
  assert.equal(controller.effectiveControls(window).nativeTitlebar, false);
  assert.equal(controller.status().restartRequired, false);
  const calls = [];
  t.mock.method(require("../../integration/desktop/popout.cjs"), "session", () => ({
    state: async () => ({ available: true }),
    choose: async (corner) => {
      calls.push(corner);
      return { corner };
    },
  }));
  assert.deepEqual(await controller.pip({ getURL: () => "https://canary.discord.com/popout" }), {
    available: false,
  });
  window.webContents.url = "https://example.com/popout";
  assert.deepEqual(await controller.pip(window.webContents, "top-left"), { available: false });
  window.webContents.url = "https://canary.discord.com/popout";
  assert.deepEqual(await controller.pip(window.webContents, "center"), { available: false });
  assert.deepEqual(await controller.pip(window.webContents, "top-left"), { corner: "top-left" });
  assert.deepEqual(calls, ["top-left"]);
});
