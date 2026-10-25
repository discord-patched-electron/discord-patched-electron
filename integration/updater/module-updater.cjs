const { EventEmitter } = require("node:events");
const { execFile } = require("node:child_process");
const path = require("node:path");

module.exports = function createUpdater(app, state, host, root, clientRoot) {
  const events = new EventEmitter();
  events.history = [];
  const installed = Object.fromEntries(
    Object.entries(state.modules).map(([name, value]) => [
      name,
      { installedVersion: value.version },
    ]),
  );
  let checking = false;
  const api = {
    events,
    supportsEventObjects: true,
    CHECKING_FOR_UPDATES: "checking-for-updates",
    UPDATE_CHECK_FINISHED: "update-check-finished",
    DOWNLOADING_MODULE_PROGRESS: "downloading-module-progress",
    DOWNLOADING_MODULES_FINISHED: "downloading-modules-finished",
    INSTALLED_MODULE: "installed-module",
    init() {},
    getInstalled: () => ({ ...installed }),
    getModulePath: (name) =>
      Object.hasOwn(installed, name) ? path.join(host, "modules", name) : null,
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
      if (checking || process.env.DPE_CLIENT_OFFLINE === "1") return;
      checking = true;
      events.emit(api.CHECKING_FOR_UPDATES);
      execFile(
        "python3",
        [path.join(root, "integration/updater/check.py"), root, clientRoot, state.channel],
        { timeout: 900000, env: { ...process.env, LD_PRELOAD: "" } },
        (error, stdout, stderr) => {
          checking = false;
          if (stderr) console.error(stderr.trim());
          let changed = false;
          if (!error) {
            try {
              const prepared = JSON.parse(stdout);
              if (typeof prepared.host !== "string" || typeof prepared.runtime !== "string")
                throw new Error("Invalid prepared update result");
              changed = prepared.host !== host || prepared.runtime !== process.execPath;
            } catch (invalid) {
              error = invalid;
              console.error(invalid.message);
            }
          }
          const count = changed ? 1 : 0;
          events.emit(api.UPDATE_CHECK_FINISHED, {
            succeeded: !error,
            updateCount: count,
            manualRequired: false,
          });
          if (count) events.emit(api.DOWNLOADING_MODULES_FINISHED, { succeeded: 1, failed: 0 });
        },
      );
    },
  };
  const timer = setInterval(() => api.checkForUpdates(), 15 * 60 * 1000);
  timer.unref();
  return api;
};
