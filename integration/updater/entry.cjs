// Project-owned entry point. Discord's client is fetched directly on first launch.
const { app, dialog } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const patches = require("./patches.cjs");

function start() {
  const root = process.env.DPE_APP_ROOT;
  const host = process.env.DPE_CLIENT_HOST;
  if (!root || !host || !process.env.DPE_CLIENT_ROOT || !process.env.DPE_LAUNCHER) {
    throw new Error("Start Discord with the package's launch.sh launcher.");
  }
  const resources = path.join(host, "resources");
  const bootstrap = path.join(root, "bootstrap/src");
  const info = JSON.parse(fs.readFileSync(path.join(resources, "build_info.json"), "utf8"));
  const state = JSON.parse(fs.readFileSync(path.join(host, "client.json"), "utf8"));
  app.setAppPath(bootstrap);
  const relaunch = app.relaunch.bind(app);
  app.relaunch = (options = {}) =>
    relaunch({
      ...options,
      execPath: process.env.DPE_LAUNCHER,
      args: options.args ?? process.argv.slice(1),
    });

  function supply(relative, value) {
    const filename = path.join(bootstrap, relative);
    require.cache[filename] = { id: filename, filename, loaded: true, exports: value };
  }
  supply("utils/buildInfo.js", info);
  supply("updater/updater.js", { getUpdater: () => null, tryInitUpdater: () => false });
  supply(
    "updater/moduleUpdater.js",
    require("./module-updater.cjs")(app, state, host, root, process.env.DPE_CLIENT_ROOT),
  );
  supply("cmdSwitches.js", () => {});
  supply("asarUpdate.js", () => {});
  const events = new (require("node:events").EventEmitter)();
  supply("splash/index.js", {
    events,
    initSplash: () => queueMicrotask(() => events.emit("APP_SHOULD_LAUNCH")),
    pageReady: () => events.emit("APP_SHOULD_SHOW"),
    focusWindow() {},
  });
  Module.globalPaths.unshift(path.join(host, "modules"));
  for (const name of Object.keys(state.modules))
    Module.globalPaths.push(fs.realpathSync(path.join(host, "modules", name)));
  const desktopRoot = fs.realpathSync(path.join(host, "modules/discord_desktop_core"));

  const compile = Module.prototype._compile;
  Module.prototype._compile = function (source, filename) {
    if (path.dirname(filename) === bootstrap) {
      source = patches.bootstrap(
        source,
        path.basename(filename),
        path.join(root, "integration/updater/desktop-exec.cjs"),
      );
    } else if (filename === path.join(desktopRoot, "core.asar/bundle.js")) {
      source = patches.desktop(source, path.join(root, "integration/desktop/safe-storage.cjs"));
    }
    return compile.call(this, source, filename);
  };

  require(bootstrap);
}

try {
  start();
} catch (error) {
  console.error(error);
  app.whenReady().then(() => {
    dialog.showErrorBox("Discord could not start", String(error.message));
    app.exit(1);
  });
}
