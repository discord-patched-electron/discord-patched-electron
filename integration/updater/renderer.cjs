const fs = require("node:fs");
const path = require("node:path");

function source(config, version, root) {
  let script = fs.readFileSync(path.join(root, "bootstrap/src/mainWindow.js"), "utf8");
  const versionText = "'OpenAsar <channel> (<hash>)'";
  if (script.split(versionText).length !== 2) throw new Error("Unsupported OpenAsar version label");
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
    .replace(versionText, () => JSON.stringify(`OpenAsar (discord-patched-electron ${version})`))
    .replaceAll("<notrack>", String(config.noTrack !== false))
    .replaceAll("<domopt>", String(config.domOptimizer !== false))
    .replace("<css>", () =>
      String(config.css ?? "")
        .replaceAll("\\", "\\\\")
        .replaceAll("`", "\\`")
        .replaceAll("${", "\\${"),
    );
}

module.exports = function install(app, splash, config) {
  const root = process.env.DPE_APP_ROOT;
  const { release_version: version } = JSON.parse(
    fs.readFileSync(path.join(root, "runtime-update.json"), "utf8"),
  );
  const script = source(config, version || "development", root);
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
