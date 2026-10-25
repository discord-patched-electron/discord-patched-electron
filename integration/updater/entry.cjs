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
    if (filename === path.join(bootstrap, "bootstrap.js")) {
      // Use OpenAsar's startup contract without its renderer customization or
      // remotely hosted settings UI. Bootstrap updates come from our package.
      const begin = source.indexOf("  app.on('browser-window-created'");
      const end = source.indexOf("  desktopCore = require('discord_desktop_core');", begin);
      if (begin < 0 || end < 0) throw new Error("Unsupported pinned OpenAsar bootstrap");
      source =
        source.slice(0, begin) +
        "  app.on('browser-window-created', (_event, window) => { window.webContents.once('did-finish-load', () => splash.pageReady()); });\n" +
        source.slice(end);
    } else if (filename === path.join(bootstrap, "index.js")) {
      source = source.replace(
        "global.oaConfig = settings.get('openasar', {});",
        "global.oaConfig = { setup: true, autoupdate: false, noTrack: false };",
      );
    } else if (filename === path.join(bootstrap, "autoStart.js")) {
      source = source.replace(
        "const exec = app.getPath('exe');",
        "const exec = process.env.DPE_LAUNCHER;",
      );
      // Desktop Entry Exec values have two escaping layers and expand % codes.
      source = source.replace(
        "Exec=${exec}",
        "Exec=${require(" +
          JSON.stringify(path.join(root, "integration/updater/desktop-exec.cjs")) +
          ")(exec)}",
      );
    } else if (filename.startsWith(process.env.DPE_CLIENT_ROOT + path.sep)) {
      if (filename === path.join(desktopRoot, "core.asar/bundle.js")) {
        source = patches.desktop(source, path.join(root, "integration/desktop/safe-storage.cjs"));
      }
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
