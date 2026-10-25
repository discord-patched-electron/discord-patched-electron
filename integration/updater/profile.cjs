const fs = require("node:fs");
const path = require("node:path");

function readObject(filename) {
  try {
    const value = JSON.parse(fs.readFileSync(filename, "utf8"));
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

function settings(profile = process.env.DPE_PROFILE_DIR) {
  return readObject(path.join(profile, "settings.json"));
}

function config(profile = process.env.DPE_PROFILE_DIR) {
  const value = settings(profile).openasar;
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function theme(window, profile = process.env.DPE_PROFILE_DIR) {
  if (config(profile).themeSync === false) return;
  const css = readObject(path.join(profile, "userDataCache.json")).openasarSplashCSS;
  if (typeof css === "string" && css) void window.webContents.insertCSS(css).catch(console.error);
}

function paths(app, info, host) {
  const profile = process.env.DPE_PROFILE_DIR;
  const versioned = path.join(profile, info.version);
  const modules = info.localModulesRoot ?? path.join(profile, "module_data");
  const logs = path.join(profile, "logs");
  const assets = path.join(profile, "discord_asset_cache");
  return {
    init() {
      for (const directory of [profile, versioned, modules, logs, assets])
        fs.mkdirSync(directory, { recursive: true });
      app.setPath("userData", profile);
      global.moduleDataPath = modules;
      global.logPath = logs;
      global.assetCachePath = assets;
    },
    getUserData: () => profile,
    getUserDataVersioned: () => versioned,
    getModuleDataPath: () => modules,
    getResources: () => path.join(host, "resources"),
    getLogPath: () => logs,
    getAssetCachePath: () => assets,
    getInstallPath: () => profile,
    getRootPath: () => profile,
    getExeDir: () => host,
    updatePathsForNewUpdater() {},
    cleanOldVersions() {},
  };
}

module.exports = { readObject, settings, config, theme, paths };
