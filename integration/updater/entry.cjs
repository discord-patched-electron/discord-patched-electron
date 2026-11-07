// Project-owned entry point. Discord's client is fetched directly on first launch.
const { app, dialog } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
require("./diagnostic-log.cjs").install(app);
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
  require("../desktop/preferences.cjs").install(require("electron"));
  require("./tray.cjs").setDefaultDirectory(
    path.join(
      installed,
      "modules",
      "discord_desktop_core-" + state.modules.discord_desktop_core.version,
      "discord_desktop_core/app/images/systemtray",
      process.platform,
    ),
  );
  app.setAppPath(bootstrap);
  const relaunch = app.relaunch.bind(app);
  app.relaunch = (options = {}) =>
    relaunch({
      ...options,
      execPath: process.env.DPE_LAUNCHER,
      args: options.args ?? require("./relaunch.cjs").arguments(),
    });

  const existingProfile = fs.existsSync(path.join(process.env.DPE_PROFILE_DIR, "settings.json"));
  const mods = require("./mod-settings.cjs");
  if (mods.needsOnboarding({ existingProfile })) {
    // Do not evaluate the Discord bootstrap or create its window before Done.
    // Welcome finishes by relaunching so new mod loaders and launch flags apply.
    require("./profile.cjs").paths(app, info, installed).init();
    require("./window.cjs"); // Register the local scheme before Electron becomes ready.
    if (!require("./profile.cjs").config().multiInstance && !app.requestSingleInstanceLock()) {
      app.exit(0);
      return;
    }
    app.on("second-instance", () => {
      const current = mods.activeWindow();
      if (current?.isVisible()) {
        current.show();
        current.focus();
      }
    });
    void app
      .whenReady()
      .then(() => mods.onboard())
      .catch(fail);
    return;
  }

  function supply(relative, value) {
    const filename = path.join(bootstrap, relative);
    require.cache[filename] = { id: filename, filename, loaded: true, exports: value };
  }
  supply("utils/buildInfo.js", info);
  supply("paths.js", require("./profile.cjs").paths(app, info, installed));
  const updater = require("./module-updater.cjs")(
    app,
    state,
    host,
    root,
    process.env.DPE_CLIENT_ROOT,
    path.dirname(fs.realpathSync(path.join(installed, "modules"))),
  );
  supply("updater/moduleUpdater.js", updater);
  supply("updater/updater.js", { getUpdater: () => updater.native, tryInitUpdater: () => false });
  supply("asarUpdate.js", () => {});
  supply("splash/index.js", require("./splash.cjs"));
  supply("config/index.js", require("./config.cjs"));
  require("./capture-backends.cjs").register();
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

  require("../desktop/spellcheck.cjs").install(require("electron"));
  require("./application-ready.cjs")(app, process.env.DPE_APPLICATION_READY, info.releaseChannel);
  require(bootstrap);
  app
    .whenReady()
    .then(() => require("./capture-permission.cjs").maintain())
    .catch((error) => {
      console.warn("DPE capture helper maintenance failed:", error.message);
    });
  if (global.oaConfig?.quickstart || process.env.OPENASAR_QUICKSTART)
    app.whenReady().then(() => setTimeout(() => updater.requestCheck(), 1000).unref());
}

function fail(error) {
  console.error(error);
  app.whenReady().then(() => {
    dialog.showErrorBox("Discord could not start", String(error.message));
    app.exit(1);
  });
}

try {
  require("../desktop/preferences.cjs").configureMediaControls(
    app.commandLine,
    require("./profile.cjs").config().desktop,
  );
  // Electron overwrites CHROME_DESKTOP from the outer loader's package.json
  // before this entry runs. Mod loaders commonly call themselves "discord".
  const desktopName = process.env.DPE_DESKTOP_ID
    ? process.env.DPE_DESKTOP_ID + ".desktop"
    : process.env.CHROME_DESKTOP;
  require("./desktop.cjs")(app, process.env.DPE_APP_ROOT, desktopName);
  if (process.env.DPE_UPDATER_UI === "1") require("./startup.cjs");
  else start();
} catch (error) {
  fail(error);
}
