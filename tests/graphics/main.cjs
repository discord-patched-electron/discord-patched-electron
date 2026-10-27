const { app, BrowserWindow, session } = require("electron");
const fs = require("node:fs"),
  path = require("node:path"),
  vm = require("node:vm");
const output = process.env.DPE_GRAPHICS_OUTPUT;
if (!output) throw new Error("A private output directory is required");
app.setPath("userData", path.join(output, "profile"));
app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");
app.commandLine.appendSwitch("disable-background-networking");
// Exercise the real pinned preset before app readiness, just like the bootstrap.
// No Discord client or account is loaded; only its command-line module runs.
const switchNames = new Set([
  "autoplay-policy",
  "disable-background-networking",
  ...process.argv.filter((arg) => arg.startsWith("--")).map((arg) => arg.slice(2).split("=", 1)[0]),
]);
const presetSource = path.join(__dirname, "cmdSwitches.js");
if (fs.existsSync(presetSource)) {
  const { bootstrap } = require("./integration/updater/patches.cjs");
  const commandLine = {
    hasSwitch: (name) => app.commandLine.hasSwitch(name),
    getSwitchValue: (name) => app.commandLine.getSwitchValue(name),
    appendSwitch: (name, value) => {
      switchNames.add(name);
      app.commandLine.appendSwitch(name, value);
    },
  };
  const context = {
    module: { exports: {} },
    process: { platform: process.platform, env: { DPE_APP_ROOT: __dirname } },
    oaConfig: { cmdPreset: process.env.DPE_GRAPHICS_PRESET },
    settings: { get: (_name, fallback) => fallback },
    require: (name) => {
      if (name === "electron") return { app: { commandLine } };
      if (name === "./paths")
        return { getAssetCachePath: () => path.join(output, "profile/discord_asset_cache") };
      return require(name);
    },
  };
  vm.runInNewContext(bootstrap(fs.readFileSync(presetSource, "utf8"), "cmdSwitches.js"), context);
  context.module.exports();
}
const effectiveSwitches = Object.fromEntries(
  [...switchNames]
    .filter((name) => app.commandLine.hasSwitch(name))
    .map((name) => [name, app.commandLine.getSwitchValue(name)]),
);
const players = {};
const gpuExits = [];
app.on("child-process-gone", (_event, details) => {
  if (details.type === "GPU" && !["clean-exit", "killed"].includes(details.reason))
    gpuExits.push(details.reason);
});
let finished = false;
let stage = "startup";
function finish(report) {
  if (finished) return;
  finished = true;
  fs.writeFileSync(
    path.join(output, "graphics.json"),
    JSON.stringify(
      { versions: process.versions, stage, players, gpuExits, effectiveSwitches, ...report },
      null,
      2,
    ),
  );
  app.exit(report.passed ? 0 : 1);
}
app
  .whenReady()
  .then(async () => {
    stage = "window";
    session.defaultSession.webRequest.onBeforeRequest((details, callback) =>
      callback({ cancel: !details.url.startsWith("file:") }),
    );
    const window = new BrowserWindow({
      show: true,
      width: 640,
      height: 480,
      title: "Discord graphics check",
      webPreferences: { sandbox: true, contextIsolation: true, backgroundThrottling: false },
    });
    window.webContents.on("render-process-gone", (_event, detail) =>
      finish({ passed: false, error: detail.reason }),
    );
    const clip = JSON.parse(fs.readFileSync(path.join(__dirname, "clip.json"), "utf8"));
    await window.loadFile(path.join(__dirname, "index.html"), { query: { clip: clip.file } });
    stage = "media-debugger";
    window.webContents.debugger.attach("1.3");
    window.webContents.debugger.on("message", (_event, method, params) => {
      if (method === "Media.playerPropertiesChanged") {
        const properties = (players[params.playerId] ??= {});
        for (const { name, value } of params.properties) properties[name] = value;
      }
    });
    await window.webContents.debugger.sendCommand("Media.enable");
    stage = "renderer";
    const rendering = await window.webContents.executeJavaScript(
      fs.readFileSync(path.join(__dirname, "renderer.js"), "utf8"),
    );
    stage = "gpu-info";
    const gpu = await app.getGPUInfo("complete");
    stage = "complete";
    finish({
      passed:
        rendering.webglPassed &&
        rendering.videoFrames >= 5 &&
        !gpuExits.length &&
        rendering.encoders.some((e) => e.chunks >= 12) &&
        !rendering.encoders.some((e) => e.error),
      rendering,
      gpuFeatures: app.getGPUFeatureStatus(),
      gpu,
      ozoneSwitch: app.commandLine.getSwitchValue("ozone-platform"),
      hardwareAccelerationEnabled: app.isHardwareAccelerationEnabled(),
      note: "Synthetic video and WebGL on the local GPU; no real Discord call.",
    });
  })
  .catch((error) => finish({ passed: false, error: error.stack || String(error) }));
setTimeout(() => finish({ passed: false, error: "Graphics probe timed out" }), 30000);
