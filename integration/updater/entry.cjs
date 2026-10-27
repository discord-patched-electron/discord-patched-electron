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
  const installed = path.join(process.env.DPE_PROFILE_DIR, "app-" + info.version);
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
  supply("paths.js", require("./profile.cjs").paths(app, info, installed));
  supply("updater/updater.js", { getUpdater: () => null, tryInitUpdater: () => false });
  const updater = require("./module-updater.cjs")(
    app,
    state,
    host,
    root,
    process.env.DPE_CLIENT_ROOT,
    path.dirname(fs.realpathSync(path.join(installed, "modules"))),
  );
  supply("updater/moduleUpdater.js", updater);
  supply("asarUpdate.js", () => {});
  supply("splash/index.js", require("./splash.cjs"));
  supply("config/index.js", require("./config.cjs"));
  const modules = Object.fromEntries(
    Object.entries(state.modules).map(([name, source]) => [
      name,
      fs.realpathSync(path.join(installed, "modules", name + "-" + source.version, name)),
    ]),
  );
  for (const module of Object.values(modules)) Module.globalPaths.push(path.dirname(module));
  const desktopRoot = modules.discord_desktop_core;

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

  require("./application-ready.cjs")(app, process.env.DPE_APPLICATION_READY, info.releaseChannel);
  require(bootstrap);
  if (global.oaConfig?.quickstart || process.env.OPENASAR_QUICKSTART)
    app.whenReady().then(() => setTimeout(() => updater.checkForUpdates(), 1000).unref());
}

try {
  if (process.env.DPE_UPDATER_UI === "1") require("./startup.cjs");
  else start();
} catch (error) {
  console.error(error);
  app.whenReady().then(() => {
    dialog.showErrorBox("Discord could not start", String(error.message));
    app.exit(1);
  });
}
