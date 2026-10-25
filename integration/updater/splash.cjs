const { app } = require("electron");
const { EventEmitter } = require("node:events");
const createWindow = require("./window.cjs");
const events = new EventEmitter();
let view,
  ready = false;

module.exports = {
  events,
  initSplash() {
    view = createWindow(() => app.quit());
    view.update({ stage: "ready", label: "Starting Discord" });
    view.window.once("closed", () => {
      if (!ready) app.quit();
    });
    queueMicrotask(() => events.emit("APP_SHOULD_LAUNCH"));
    if (global.oaConfig?.quickstart || process.env.OPENASAR_QUICKSTART)
      setTimeout(() => module.exports.pageReady(), 300);
  },
  pageReady() {
    if (ready) return;
    ready = true;
    view?.window.close();
    events.emit("APP_SHOULD_SHOW");
  },
  focusWindow() {
    if (view && !view.window.isDestroyed()) view.window.focus();
  },
};
