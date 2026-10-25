const fs = require("node:fs");
const path = require("node:path");

function source(config, version, root) {
  const [channel = "", hash = ""] = version.split("-");
  let script = fs.readFileSync(path.join(root, "bootstrap/src/mainWindow.js"), "utf8");
  // Only the theme collector is replaced; retain OpenAsar's settings entry,
  // custom CSS, privacy controls and optional DOM optimizer.
  const begin = script.indexOf("let lastBgPrimary = '';");
  const end = script.indexOf("// Settings injection");
  if (begin < 0 || end <= begin) throw new Error("Unsupported OpenAsar theme collector");
  script =
    script.slice(0, begin) +
    fs.readFileSync(path.join(__dirname, "theme-sync.js"), "utf8") +
    "\n" +
    script.slice(end);
  return script
    .replaceAll("<hash>", hash)
    .replaceAll("<channel>", channel === "nightly" ? "" : channel)
    .replaceAll("<notrack>", String(config.noTrack !== false))
    .replaceAll("<domopt>", String(config.domOptimizer !== false))
    .replace("<css>", () =>
      String(config.css ?? "")
        .replaceAll("\\", "\\\\")
        .replaceAll("`", "\\`")
        .replaceAll("${", "\\${"),
    );
}

module.exports = function install(app, splash, config, version) {
  const script = source(config, version, process.env.DPE_APP_ROOT);
  app.on("browser-window-created", (_event, window) => {
    window.webContents.on("did-finish-load", () => {
      const address = window.webContents.getURL();
      if (!URL.canParse(address)) return;
      const url = new URL(address);
      if (
        url.protocol !== "https:" ||
        !["discord.com", "canary.discord.com", "ptb.discord.com"].includes(url.hostname)
      )
        return;
      splash.pageReady();
      void window.webContents.executeJavaScript(script + "\nvoid 0;").catch(console.error);
      if (typeof config.js === "string" && config.js)
        void window.webContents.executeJavaScript(config.js).catch(console.error);
    });
  });
};
module.exports.source = source;
