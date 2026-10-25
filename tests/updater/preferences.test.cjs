const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const preferences = require("../../integration/updater/profile.cjs");
const renderer = require("../../integration/updater/renderer.cjs");
const append = require("../../integration/updater/flags.cjs");

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "openasar-prefs-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return directory;
}

test("saved preferences and cached theme share Discord's profile and honor themeSync", (t) => {
  const profile = fixture(t);
  const settings = { openasar: { themeSync: true, css: "body {color:red}", noTrack: false } };
  fs.writeFileSync(path.join(profile, "settings.json"), JSON.stringify(settings));
  fs.writeFileSync(
    path.join(profile, "userDataCache.json"),
    JSON.stringify({ openasarSplashCSS: "body {background:red}" }),
  );
  const inserted = [];
  const window = { webContents: { insertCSS: async (value) => inserted.push(value) } };
  assert.deepEqual(preferences.config(profile), settings.openasar);
  preferences.theme(window, profile);
  settings.openasar.themeSync = false;
  fs.writeFileSync(path.join(profile, "settings.json"), JSON.stringify(settings));
  preferences.theme(window, profile);
  assert.deepEqual(inserted, ["body {background:red}"]);
  assert.deepEqual(preferences.settings(profile), settings);
});

test("all native bootstrap paths use the canonical profile and app tree", (t) => {
  const profile = fixture(t);
  const previous = process.env.DPE_PROFILE_DIR;
  process.env.DPE_PROFILE_DIR = profile;
  t.after(() => {
    if (previous === undefined) delete process.env.DPE_PROFILE_DIR;
    else process.env.DPE_PROFILE_DIR = previous;
  });
  const calls = [];
  const paths = preferences.paths(
    { setPath: (...args) => calls.push(args) },
    { version: "1.0.9" },
    path.join(profile, "app-1.0.9"),
  );
  paths.init();
  for (const [getter, relative] of Object.entries({
    getUserData: "",
    getUserDataVersioned: "1.0.9",
    getModuleDataPath: "module_data",
    getLogPath: "logs",
    getAssetCachePath: "discord_asset_cache",
    getInstallPath: "",
    getRootPath: "",
    getExeDir: "app-1.0.9",
    getResources: "app-1.0.9/resources",
  }))
    assert.equal(paths[getter](), path.join(profile, relative));
  assert.deepEqual(calls, [["userData", profile]]);
  for (const directory of ["1.0.9", "module_data", "logs", "discord_asset_cache"])
    assert.ok(fs.statSync(path.join(profile, directory)).isDirectory());
});

test("custom CSS cannot become template-literal JavaScript", (t) => {
  const root = fixture(t);
  const directory = path.join(root, "bootstrap/src");
  fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(
    path.join(directory, "mainWindow.js"),
    "let lastBgPrimary = '';\noldCollector();\n// Settings injection\nresult = `<css>`;",
  );
  const css = 'body::before {content:"${globalThis.executed=true} `$& \\"}';
  const context = { result: "" };
  vm.runInNewContext(renderer.source({ css }, "nightly-test", root), context);
  assert.equal(context.result, css);
  assert.equal(context.executed, undefined);
});

test("current and legacy theme tokens update the cache without losing other user data", async () => {
  const body = { parentElement: null };
  let values = {
    "--background-base-lowest": "#ffffff",
    "--text-default": "#111111",
    "--font-primary": "gg sans",
  };
  let cached = { existing: "preserved" };
  const writes = [];
  const context = {
    document: { body },
    getComputedStyle: () => ({ getPropertyValue: (name) => values[name] || "" }),
    DiscordNative: {
      userDataCache: {
        getCached: async () => cached,
        cacheUserData: async (value) => {
          cached = JSON.parse(value);
          writes.push(cached);
        },
      },
    },
  };
  vm.createContext(context);
  vm.runInContext(
    fs.readFileSync(path.join(__dirname, "../../integration/updater/theme-sync.js"), "utf8"),
    context,
  );
  await vm.runInContext("themesync()", context);
  assert.equal(cached.existing, "preserved");
  assert.match(cached.openasarSplashCSS, /--background-primary: #ffffff/);
  assert.ok(!cached.openasarSplashCSS.includes("--font-primary"));
  await vm.runInContext("themesync()", context);
  assert.equal(writes.length, 1);
  values = {
    "--background-primary": "#123456",
    "--background-base-lowest": "#ffffff",
    "--header-primary": "white",
  };
  await vm.runInContext("themesync()", context);
  assert.match(cached.openasarSplashCSS, /--background-primary: #123456/);
});

test("OpenAsar presets respect explicit feature and scalar flags", () => {
  const switches = {
    "enable-features": "Extra,Keep:Param/value",
    "disable-features": "Vulkan",
    "ozone-platform": "wayland",
  };
  const commandLine = {
    getSwitchValue: (name) => switches[name] || "",
    hasSwitch: (name) => Object.hasOwn(switches, name),
    appendSwitch: (name, value) => (switches[name] = value),
  };
  append(commandLine, "enable-features", "Vulkan,Keep:Other/value,CanvasOopRasterization");
  append(commandLine, "disable-features", "Extra,HardwareMediaKeyHandling");
  append(commandLine, "ozone-platform", "x11");
  append(commandLine, "force_low_power_gpu", "");
  assert.equal(switches["enable-features"], "Extra,Keep:Param/value,CanvasOopRasterization");
  assert.equal(switches["disable-features"], "Vulkan,HardwareMediaKeyHandling");
  assert.equal(switches["ozone-platform"], "wayland");
  assert.ok(Object.hasOwn(switches, "force_low_power_gpu"));
});
