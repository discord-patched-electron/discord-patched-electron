// Native desktop behavior. The paired source transform calls these helpers at
// Discord's existing integration points; no Electron prototypes are replaced.
const { execFile } = require("node:child_process");
const DEFAULTS = Object.freeze({
  nativeTitlebar: false,
  windowControls: true,
  minimizeButton: true,
  maximizeButton: true,
  closeButton: true,
  minimumWindowSize: true,
  tray: true,
  notificationFlash: true,
  appBadge: true,
  badgeOnlyMentions: false,
  startMinimized: false,
  discordLinks: false,
});

function normalize(value = {}) {
  const result = { ...DEFAULTS };
  if (!value || typeof value !== "object" || Array.isArray(value)) return result;
  for (const [key, fallback] of Object.entries(DEFAULTS)) {
    if (!Object.hasOwn(value, key)) continue;
    if (typeof fallback === "boolean" && typeof value[key] === "boolean") result[key] = value[key];
  }
  return result;
}

const token = (name) => `:is([class^="${name}_"],[class*=" ${name}_"])`;
function titlebarCSS(value) {
  const settings = normalize(value);
  const buttons = token("winButtons");
  const legacy = token("titleBar");
  const button = token("winButton");
  if (
    settings.nativeTitlebar ||
    !settings.windowControls ||
    (!settings.minimizeButton && !settings.maximizeButton && !settings.closeButton)
  )
    // Discord's Linux/Windows rule removes the bar's normal trailing inset
    // so its native-style buttons meet the edge. Restore the upstream inset
    // when those buttons (and their divider) are removed, leaving Help/Inbox
    // aligned to the same spacing tokens as the rest of the bar.
    return (
      `${buttons},${legacy}>${button}{display:none!important}` +
      `${token("bar")}:has(>${token("trailing")} ${buttons}){padding-inline-end:var(--space-12,12px)!important}`
    );
  // These are Discord's existing three window actions, in their native order.
  // Restrict the selector to their own container and class token; ordinary
  // buttons, the app toolbar and activity content are never selected.
  const hidden = [settings.minimizeButton, settings.maximizeButton, settings.closeButton].flatMap(
    (enabled, index) =>
      enabled
        ? []
        : [
            `${buttons}>${button}:nth-child(${index + 1} of ${button})`,
            // Discord's retained legacy titlebar floats its close/max/min children
            // in the reverse order. Its wordmark and other children stay intact.
            `${legacy}>${button}:nth-child(${3 - index} of ${button})`,
          ],
  );
  return hidden.length ? `${hidden.join(",")}{display:none!important}` : "";
}

function createController(electron, getConfig, { argv = process.argv } = {}) {
  let values = normalize(getConfig());
  const windows = new Map();
  const subscriptions = new Set();
  let tray;
  let lastBadge;
  let lastDockBadge;
  let overlayRefresh;

  function status() {
    return {
      values: { ...values },
      restartRequired: [...windows.values()].some(
        (state) => state.managed && state.nativeTitlebar !== values.nativeTitlebar,
      ),
    };
  }
  const minimum = (size) => (values.minimumWindowSize ? size : 1);
  const badgeCount = (count) =>
    !values.appBadge || (values.badgeOnlyMentions && count < 0) ? 0 : count;
  const dockBadge = (text) =>
    !values.appBadge || (values.badgeOnlyMentions && !/^\d+$/.test(text)) ? "" : text;

  function effectiveControls(window) {
    const nativeTitlebar = values.nativeTitlebar || windows.get(window)?.nativeTitlebar === true;
    const windowControls = !nativeTitlebar && values.windowControls;
    return {
      nativeTitlebar,
      windowControls,
      minimizeButton: windowControls && values.minimizeButton,
      maximizeButton: windowControls && values.maximizeButton,
      closeButton: windowControls && values.closeButton,
    };
  }

  function subscribeControls(window, callback) {
    const subscription = { window, callback, previous: "" };
    subscriptions.add(subscription);
    const dispose = () => {
      subscriptions.delete(subscription);
      window.removeListener("closed", dispose);
    };
    window.once("closed", dispose);
    publishControls(subscription);
    return dispose;
  }

  function publishControls(subscription) {
    if (subscription.window.isDestroyed()) return;
    const value = effectiveControls(subscription.window);
    const identity = JSON.stringify(value);
    if (identity === subscription.previous) return;
    subscription.previous = identity;
    subscription.callback(value);
  }

  function updateNativeControls(window, state) {
    if (!state.nativeControls || state.excluded || window.isDestroyed()) return;
    // Native titlebars replace custom chrome and retain their own original
    // buttons. Other native-framed windows follow the same individual toggles.
    const original = state.nativeControls;
    const enabled = values.nativeTitlebar || values.windowControls;
    const next = Object.fromEntries(
      ["minimize", "maximize", "close"].map((action) => [
        action,
        original[action] && (values.nativeTitlebar || (enabled && values[action + "Button"])),
      ]),
    );
    const identity = JSON.stringify(next);
    if (state.nativeControlsIdentity === identity) return;
    window.setWindowControls(next);
    state.nativeControlsIdentity = identity;
  }

  function updateCSS(window, state) {
    // Serialize stylesheet swaps so a slow insert from an earlier save cannot
    // restore hidden controls after the newest setting has been applied.
    state.cssTask = (state.cssTask || Promise.resolve())
      .catch(() => {})
      .then(async () => {
        const web = window.webContents;
        if (window.isDestroyed() || web.isDestroyed()) return;
        const css =
          !state.excluded && web.getURL()
            ? titlebarCSS({
                ...values,
                nativeTitlebar: state.nativeTitlebar || values.nativeTitlebar,
              })
            : "";
        if (css === state.css) return;
        const previous = state.cssKey;
        const next = css ? await web.insertCSS(css) : undefined;
        if (previous) await web.removeInsertedCSS(previous);
        state.css = css;
        state.cssKey = next;
      });
    state.cssTask = state.cssTask.catch((error) => {
      // Navigation/closing can destroy the target between async operations.
      if (!window.isDestroyed() && !window.webContents.isDestroyed()) throw error;
    });
    return state.cssTask;
  }

  function observeWindow(window) {
    if (windows.has(window)) return window;
    const state = {
      nativeTitlebar: false,
      managed: false,
      nativeControls:
        typeof window.getWindowControls === "function" &&
        typeof window.setWindowControls === "function"
          ? window.getWindowControls()
          : undefined,
    };
    windows.set(window, state);
    updateNativeControls(window, state);
    window.once("closed", () => windows.delete(window));
    window.webContents.on("did-start-navigation", (_event, _url, inPlace, mainFrame) => {
      if (!mainFrame || inPlace) return;
      state.css = undefined;
      state.cssKey = undefined;
    });
    window.webContents.on("dom-ready", () => {
      void updateCSS(window, state).catch(console.error);
    });
    if (window.webContents.getURL()) void updateCSS(window, state).catch(console.error);
    window.on("focus", () => window.flashFrame(false));
    return window;
  }

  function attach(window, options, main = false) {
    observeWindow(window);
    if (options.outOfProcessOverlay) {
      const state = windows.get(window);
      state.excluded = true;
      if (state.nativeControls) window.setWindowControls(state.nativeControls);
      void updateCSS(window, state).catch(console.error);
      return window;
    }
    Object.assign(windows.get(window), {
      nativeTitlebar: values.nativeTitlebar,
      managed: true,
      minimum: [options.minWidth || (main ? 940 : 1), options.minHeight || (main ? 500 : 1)],
      main,
    });
    if (!values.minimumWindowSize) window.setMinimumSize(1, 1);
    return window;
  }

  function windowOptions(options) {
    if (options.outOfProcessOverlay) return options;
    const result = {
      ...options,
      minWidth: minimum(options.minWidth || 1),
      minHeight: minimum(options.minHeight || 1),
    };
    if (values.nativeTitlebar) {
      result.frame = true;
      result.titleBarStyle = "default";
      delete result.titleBarOverlay;
      delete result.trafficLightPosition;
    }
    return result;
  }

  function minimizeWithoutTray(window) {
    if (values.tray || !window || window.isDestroyed()) return false;
    window.setSkipTaskbar(false);
    window.showInactive();
    window.minimize();
    return true;
  }

  function setMinimumSize(window, width, height) {
    const state = windows.get(window);
    if (state) state.minimum = [width, height];
    window.setMinimumSize(minimum(width), minimum(height));
  }

  function claimLinks() {
    if (
      values.discordLinks &&
      !electron.app.isDefaultProtocolClient("discord") &&
      !electron.app.setAsDefaultProtocolClient("discord")
    )
      throw new Error("Could not make this app open Discord links.");
  }

  function linksElsewhere() {
    if (electron.app.isDefaultProtocolClient("discord")) return Promise.resolve(false);
    return new Promise((resolve) =>
      execFile(
        "xdg-mime",
        ["query", "default", "x-scheme-handler/discord"],
        { timeout: 5000 },
        (error, stdout) => resolve(Boolean(error) || stdout.trim() !== ""),
      ),
    );
  }

  async function apply(next) {
    values = normalize(next);
    const updates = [];
    for (const [window, state] of windows) {
      if (window.isDestroyed() || state.excluded) continue;
      updateNativeControls(window, state);
      if (state.minimum) window.setMinimumSize(...state.minimum.map(minimum));
      if (values.minimumWindowSize && state.minimum) {
        const [width, height] = window.getSize();
        if (width < state.minimum[0] || height < state.minimum[1])
          window.setSize(Math.max(width, state.minimum[0]), Math.max(height, state.minimum[1]));
      }
      if (!values.notificationFlash) window.flashFrame(false);
      if (!values.tray && state.main && !window.isVisible()) minimizeWithoutTray(window);
      updates.push(updateCSS(window, state));
    }
    if (tray) {
      if (values.tray) tray.create();
      else tray.destroy();
    }
    if (lastBadge) lastBadge.app.setBadgeCount(badgeCount(lastBadge.count));
    else if (!values.appBadge && electron.app.isReady()) electron.app.setBadgeCount(0);
    if (lastDockBadge) lastDockBadge.dock.setBadge(dockBadge(lastDockBadge.text));
    overlayRefresh?.();
    await Promise.all(updates);
    for (const subscription of subscriptions.values()) publishControls(subscription);
    return status();
  }

  return {
    status,
    apply,
    claimLinks,
    linksElsewhere,
    effectiveControls,
    subscribeControls,
    observeWindow,
    minimum,
    badgeCount,
    windowOptions,
    minimizeWithoutTray,
    startMinimized: () => values.startMinimized || argv.includes("--start-minimized"),
    createMainWindow: (BrowserWindow, options) =>
      attach(new BrowserWindow(windowOptions(options)), options, true),
    createAuxiliaryWindow: (BrowserWindow, options) =>
      attach(new BrowserWindow(windowOptions(options)), options),
    attachPopout: (window, options) =>
      attach(window, {
        ...options,
        minWidth: Math.max(320, options.minWidth || 0),
        minHeight: Math.max(180, options.minHeight || 0),
      }),
    minimizeToTray: (enabled) => values.tray && enabled,
    trayEnabled: () => values.tray,
    registerTray: (create, destroy) => {
      tray = { create, destroy };
    },
    setMinimumSize,
    flashFrame: (window, flag) => window.flashFrame(Boolean(flag && values.notificationFlash)),
    setBadgeCount: (app, count) => {
      lastBadge = { app, count };
      return app.setBadgeCount(badgeCount(count));
    },
    setDockBadge: (dock, text) => {
      lastDockBadge = { dock, text };
      dock.setBadge(dockBadge(text));
    },
    rememberOverlay: (refresh) => {
      overlayRefresh = refresh;
    },
  };
}

function storedConfig() {
  return (
    global.settings?.get
      ? global.settings.get("openasar", {})
      : require("../updater/profile.cjs").config()
  ).desktop;
}
let controller,
  installed = false;
function getController() {
  return controller || install(require("electron"));
}
function install(electron, { getConfig = storedConfig } = {}) {
  controller ||= createController(electron, getConfig);
  if (!installed) {
    installed = true;
    electron.app.on("browser-window-created", (_event, window) => controller.observeWindow(window));
    for (const window of electron.BrowserWindow.getAllWindows()) controller.observeWindow(window);
  }
  return controller;
}
module.exports = { DEFAULTS, normalize, titlebarCSS, createController, install };
for (const name of [
  "status",
  "apply",
  "claimLinks",
  "linksElsewhere",
  "effectiveControls",
  "subscribeControls",
  "minimum",
  "badgeCount",
  "windowOptions",
  "minimizeWithoutTray",
  "startMinimized",
  "createMainWindow",
  "createAuxiliaryWindow",
  "attachPopout",
  "minimizeToTray",
  "trayEnabled",
  "registerTray",
  "setMinimumSize",
  "flashFrame",
  "setBadgeCount",
  "setDockBadge",
  "rememberOverlay",
])
  module.exports[name] = (...args) => getController()[name](...args);
