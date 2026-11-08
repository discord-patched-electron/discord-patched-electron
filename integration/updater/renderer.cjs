const fs = require("node:fs");
const path = require("node:path");

function source(config, root) {
  let script = fs.readFileSync(path.join(root, "bootstrap/src/mainWindow.js"), "utf8");
  const versionText = "'OpenAsar <channel> (<hash>)'";
  if (script.split(versionText).length !== 2) throw new Error("Unsupported OpenAsar version label");
  // Replace the theme collector and settings entry; retain
  // custom CSS, privacy controls and optional DOM optimizer.
  const begin = script.indexOf("let lastBgPrimary = '';");
  const end = script.indexOf("// Settings injection");
  if (begin < 0 || end <= begin) throw new Error("Unsupported OpenAsar theme collector");
  script =
    script.slice(0, begin) +
    fs.readFileSync(path.join(__dirname, "theme-sync.js"), "utf8") +
    "\n" +
    script.slice(end);
  const injectionEnd = script.indexOf("const injCSS =", script.indexOf("// Settings injection"));
  if (injectionEnd < 0) throw new Error("Unsupported OpenAsar settings injection");
  script =
    script.slice(0, script.indexOf("// Settings injection")) +
    require("../discord-ui/source.cjs").call(
      require("../discord-ui/startup-presentation.cjs"),
      {},
      require("../discord-ui/presentation.cjs"),
    ) +
    ";\n" +
    require("../discord-ui/source.cjs").call(require("../discord-ui/settings.cjs"), {
      version: require("./presentation.cjs").metadata(root).dpe,
    }) +
    ";\n" +
    require("../discord-ui/source.cjs").call(require("../video/bitrate.cjs"), {
      platform: process.platform,
      maxBitrate: Number(process.env.DPE_SCREENSHARE_MAX_BITRATE),
      preserveViewerBitrate: require("../video/settings.cjs").normalize(config.screensharing)
        .preserveViewerBitrate,
    }) +
    ";\n" +
    require("../discord-ui/source.cjs").call(require("../video/codecs.cjs"), {
      platform: process.platform,
      excludedCodecs: require("../video/settings.cjs").normalize(config.screensharing)
        .excludedCodecs,
    }) +
    ";\n" +
    require("../discord-ui/source.cjs").call(
      require("../video/capture-policy.cjs"),
      require("./screensharing.cjs").policy(config.screensharing),
    ) +
    ";\n" +
    require("../discord-ui/source.cjs").call(
      require("../discord-ui/capture-picker.cjs"),
      {
        ...require("./screensharing.cjs").policy(config.screensharing),
        styles: fs.readFileSync(path.join(__dirname, "../discord-ui/components.css"), "utf8"),
        icons: require("../discord-ui/icons.cjs")(),
      },
      require("../discord-ui/capture-picker-view.cjs"),
    ) +
    ";\n" +
    script.slice(injectionEnd);
  return script
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
  const script = source(config, root);
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
