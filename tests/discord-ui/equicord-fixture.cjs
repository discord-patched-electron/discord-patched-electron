// Exercise the real mod loader using empty settings, never the user's mod data.
const fs = require("node:fs");
const path = require("node:path");
const { ipcMain } = require("electron");
module.exports = function equicordFixture(archive, originalPreload) {
  process.env.DISCORD_PRELOAD = originalPreload;
  const sync = {
    VencordGetPluginIpcMethodMap: {},
    VencordGetSettings: { autoUpdate: false, autoUpdateNotification: false, plugins: {} },
    VencordPreloadGetRendererJs: fs.readFileSync(path.join(archive, "renderer.js"), "utf8"),
    VencordSupportsWindowsMaterial: false,
  };
  for (const [channel, value] of Object.entries(sync))
    ipcMain.on(channel, (event) => {
      event.returnValue = value;
    });
  const async = {
    VencordInitFileWatchers: undefined,
    VencordGetQuickCss: "",
    VencordGetRendererCss: fs.readFileSync(path.join(archive, "renderer.css"), "utf8"),
    VencordGetThemesList: [],
    VencordGetThemeSystemValues: {},
    VencordSetSettings: undefined,
  };
  for (const [channel, value] of Object.entries(async)) ipcMain.handle(channel, () => value);
  return path.join(archive, "preload.js");
};
