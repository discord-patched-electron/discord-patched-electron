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
      if (task) return task.result;
      if (stopped) return Promise.resolve({ succeeded: false, updateCount: 0 });
      if (process.env.DPE_CLIENT_OFFLINE === "1")
        return Promise.resolve({ succeeded: true, updateCount: 0 });
      events.emit(api.CHECKING_FOR_UPDATES);
      task = runUpdate({ root, clientRoot, channel: state.channel, prepareOnly: true });
      const finish = (error, prepared) => {
        task = null;
        if (stopped) return { succeeded: false, updateCount: 0 };
        if (error) console.error(error.message);
        const changed =
          !error &&
          ((prepared.application && prepared.application !== root) ||
            prepared.host !== host ||
            prepared.runtime !== (process.env.DPE_RUNTIME_SOURCE || process.execPath));
        const count = changed ? 1 : 0;
        events.emit(api.UPDATE_CHECK_FINISHED, {
          succeeded: !error,
          updateCount: count,
          manualRequired: false,
        });
        if (count) events.emit(api.DOWNLOADING_MODULES_FINISHED, { succeeded: 1, failed: 0 });
        return { succeeded: !error, updateCount: count, error };
      };
      task.result = task.promise.then(
        (prepared) => finish(null, prepared),
        (error) => finish(error),
      );
      return task.result;
    },
  };
  // Desktop core 1.0.2444 only consumes the native-updater interface. Keep the
  // running generation immutable and route all preparation through DPE policy.
  const versions = () => ({
    current_host: state.version?.split(".").map(Number) ?? [],
    current_modules: Object.fromEntries(
      Object.entries(state.modules).map(([name, value]) => [name, value.version]),
    ),
    running_update: { metadata_version: 0 },
    last_successful_update: { metadata_version: 0 },
  });
  api.native = {
    committedModulePaths: new Map(
      Object.keys(installed).map((name) => [name, api.getModulePath(name)]),
    ),
    queryCurrentVersionsWithOptions: async () => versions(),
    queryCurrentVersionsWithOptionsSync: () => versions(),
    async installModule(name) {
      if (!api.isInstalled(name)) throw new Error(`Module is not in the approved client: ${name}`);
    },
    async commitModules() {},
    async updateToLatestWithOptions(_options, onProgress = () => {}) {
      const result = await api.checkForUpdates();
      if (!result.succeeded) throw result.error ?? new Error("Update preparation stopped");
      if (result.updateCount) onProgress({ task: { HostInstall: {} }, state: "Complete" });
    },
    quitAndInstallUpdates: () => api.quitAndInstallUpdates(),
    queryAndTruncateHistory: () => events.history.splice(0),
  };
  api.requestCheck = () => {
    // Let desktop core own notifications for periodic, quickstart and menu checks.
    if (app.listenerCount("menu:check-for-updates")) app.emit("menu:check-for-updates");
    else void api.checkForUpdates();
  };
  const timer = setInterval(() => api.requestCheck(), 15 * 60 * 1000);
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
