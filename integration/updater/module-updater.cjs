const { EventEmitter } = require("node:events");
const path = require("node:path");
const runUpdate = require("./runner.cjs");

module.exports = function createUpdater(app, state, host, root, clientRoot, installedHost) {
  const events = new EventEmitter();
  events.history = [];
  const installed = Object.fromEntries(
    Object.entries(state.modules).map(([name, value]) => [
      name,
      { installedVersion: value.version },
    ]),
  );
  let task,
    stopped = false;
  const api = {
    events,
    supportsEventObjects: true,
    CHECKING_FOR_UPDATES: "checking-for-updates",
    UPDATE_CHECK_FINISHED: "update-check-finished",
    DOWNLOADING_MODULE_PROGRESS: "downloading-module-progress",
    DOWNLOADING_MODULES_FINISHED: "downloading-modules-finished",
    INSTALLED_MODULE: "installed-module",
    init() {},
    getInstalled: () =>
      Object.fromEntries(Object.entries(installed).map(([name, value]) => [name, { ...value }])),
    getModulePath: (name) =>
      Object.hasOwn(installed, name)
        ? installedHost
          ? path.join(installedHost, "modules", name + "-" + state.modules[name].version, name)
          : path.join(host, "modules", name)
        : null,
    isInstalled: (name, version) =>
      Object.hasOwn(installed, name) &&
      (version == null || installed[name].installedVersion === version),
    install(name, deferred, options = {}) {
      if (!deferred)
        queueMicrotask(() =>
          events.emit(api.INSTALLED_MODULE, {
            name,
            succeeded: api.isInstalled(name, options.version),
            current: 1,
            total: 1,
          }),
        );
    },
    quitAndInstallUpdates() {
      app.relaunch();
      app.quit();
    },
    checkForUpdates() {
      if (task || stopped || process.env.DPE_CLIENT_OFFLINE === "1") return;
      events.emit(api.CHECKING_FOR_UPDATES);
      task = runUpdate({ root, clientRoot, channel: state.channel, prepareOnly: true });
      const finish = (error, prepared) => {
        task = null;
        if (stopped) return;
        if (error) console.error(error.message);
        const changed =
          !error &&
          (prepared.host !== host ||
            prepared.runtime !== (process.env.DPE_RUNTIME_SOURCE || process.execPath));
        const count = changed ? 1 : 0;
        events.emit(api.UPDATE_CHECK_FINISHED, {
          succeeded: !error,
          updateCount: count,
          manualRequired: false,
        });
        if (count) events.emit(api.DOWNLOADING_MODULES_FINISHED, { succeeded: 1, failed: 0 });
      };
      task.promise.then(
        (prepared) => finish(null, prepared),
        (error) => finish(error),
      );
    },
  };
  const timer = setInterval(() => api.checkForUpdates(), 15 * 60 * 1000);
  timer.unref();
  app.on("before-quit", (event) => {
    stopped = true;
    clearInterval(timer);
    if (!task) return;
    event.preventDefault();
    task.cancel();
    task.promise.catch(() => {}).then(() => app.quit());
  });
  return api;
};
