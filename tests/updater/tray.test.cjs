const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const { createHash } = require("node:crypto");
const { patch } = require("../../integration/updater/tray-patch.cjs");
const presetRoot = path.join(__dirname, "../../integration/updater/assets/tray-presets");
const manifest = require(path.join(presetRoot, "manifest.json"));

function fixture(t) {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "dpe-tray-"));
  t.after(() => fs.rmSync(profile, { recursive: true, force: true }));
  const png = Buffer.alloc(24);
  Buffer.from("89504e470d0a1a0a", "hex").copy(png);
  png.write("IHDR", 12);
  png.writeUInt32BE(32, 16);
  png.writeUInt32BE(32, 20);
  const source = path.join(profile, "source.png");
  fs.writeFileSync(source, png);
  let config = { themeSync: false };
  let canceled = false;
  let failSave = false;
  const presetLoads = [];
  const nativeImage = {
    isEmpty: () => false,
    resize: () => nativeImage,
    toPNG: () => png,
    toDataURL: () => "data:image/png;base64," + png.toString("base64"),
  };
  const context = {
    Buffer,
    __dirname: path.join(__dirname, "../../integration/updater"),
    process: { env: { DPE_PROFILE_DIR: profile } },
    global: {
      settings: {
        get: () => config,
        set: (_key, value) => (config = value),
        save: () => {
          if (failSave) throw new Error("Disk full");
        },
      },
    },
    module: { exports: {} },
    require(name) {
      if (name === "electron")
        return {
          nativeImage: {
            createFromBuffer: () => nativeImage,
            createFromPath: (filename) => {
              if (!filename.startsWith(presetRoot + path.sep)) return nativeImage;
              presetLoads.push(filename);
              const bundled = {
                ...nativeImage,
                filename,
                resize(options) {
                  assert.equal(options.width, 32);
                  assert.equal(options.height, 32);
                  return bundled;
                },
                toDataURL: () =>
                  "data:image/png;base64," + fs.readFileSync(filename).toString("base64"),
              };
              return bundled;
            },
          },
          dialog: { showOpenDialog: async () => ({ canceled, filePaths: [source] }) },
        };
      if (name === "./profile.cjs") return { config: () => config };
      if (name === "./assets/tray-presets/manifest.json") return manifest;
      return require(name);
    },
  };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "../../integration/updater/tray.cjs"), "utf8"),
    context,
  );
  return {
    api: context.module.exports,
    source,
    png,
    profile,
    config: () => config,
    cancel: () => (canceled = true),
    fail: () => (failSave = true),
    nativeImage,
    presetLoads,
  };
}

test("tray states retain Discord deaf/mute priority and unread indication", (t) => {
  const { api } = fixture(t);
  assert.equal(api.stateFor(), "default");
  assert.equal(api.stateFor({ unread: true }), "unread");
  assert.equal(api.stateFor({ connected: true, unread: true }), "connected");
  assert.equal(api.stateFor({ connected: true, speaking: true }), "speaking");
  assert.equal(
    api.stateFor({ connected: true, speaking: true, muteStates: { suppress: true } }),
    "muted",
  );
  assert.equal(
    api.stateFor({
      connected: true,
      muteStates: { selfMute: true },
      deafStates: { serverDeaf: true },
    }),
    "deafened",
  );
});

test("imported icons survive source deletion, update live and reset to the stock state", async (t) => {
  const f = fixture(t);
  const stock = {};
  let displayed;
  f.api.register({ isDestroyed: () => false, setImage: (value) => (displayed = value) }, () =>
    f.api.image(stock, { unread: true }),
  );
  await f.api.choose({}, "unread");
  assert.equal(displayed, f.nativeImage);
  assert.equal(f.config().themeSync, false);
  assert.match(f.config().trayIcons.unread, /^[a-f0-9]{64}\.png$/);
  fs.unlinkSync(f.source);
  assert.equal(f.api.image(stock, { unread: true }), f.nativeImage);
  assert.equal(f.api.image(stock, {}), stock);
  assert.equal(f.api.describe().find((state) => state.id === "unread").custom, true);
  f.api.reset("unread");
  assert.equal(displayed, stock);
});

test("cancellation and failed persistence preserve settings; unknown states reject", async (t) => {
  const f = fixture(t);
  f.fail();
  await assert.rejects(f.api.choose({}, "default"), /Disk full/);
  assert.equal(f.config().trayIcons, undefined);
  f.cancel();
  assert.equal((await f.api.choose({}, "default")).icons.length, 6);
  await assert.rejects(f.api.choose({}, "__proto__"), /Unknown tray/);
  assert.throws(() => f.api.reset("../escape"), /Unknown tray/);
});

test("bundled presets switch live, cache decoded icons, clear overrides and restore on reset", async (t) => {
  const f = fixture(t);
  const stock = {};
  let displayed;
  f.api.register({ isDestroyed: () => false, setImage: (value) => (displayed = value) }, () =>
    f.api.image(stock, { connected: true, speaking: true }),
  );
  const result = f.api.setPreset("equicord");
  assert.equal(result.preset, "equicord");
  assert.match(displayed.filename, /equicord\/speaking\.png$/);
  assert.equal(
    result.icons.every((icon) => icon.source === "Equicord" && !icon.custom),
    true,
  );
  assert.equal(f.presetLoads.length, 6);
  f.api.status();
  f.api.image(stock, { unread: true });
  assert.equal(f.presetLoads.length, 6, "decoded assets are reused across reads and tray events");
  await f.api.choose({}, "speaking");
  assert.equal(displayed, f.nativeImage);
  f.api.setPreset("vencord");
  assert.match(displayed.filename, /vencord\/tray\.png$/);
  assert.equal(f.config().trayIcons.speaking, undefined);
  f.api.reset("speaking");
  assert.match(displayed.filename, /vencord\/tray\.png$/);
  assert.equal(
    f.api.status().icons.every((icon) => icon.source === "Vencord"),
    true,
  );
  f.api.setPreset("discord");
  assert.equal(displayed, stock);
  assert.equal(f.config().themeSync, false);
  f.config().trayIconPreset = "../invalid";
  assert.equal(f.api.status().preset, "discord");
});

test("unknown presets and failed writes cannot change the active preset", (t) => {
  const f = fixture(t);
  for (const value of ["__proto__", "../vencord", "unknown", {}, null])
    assert.throws(() => f.api.setPreset(value), /Unknown tray icon preset/);
  f.api.setPreset("vencord");
  f.fail();
  assert.throws(() => f.api.setPreset("equicord"), /Disk full/);
  assert.equal(f.api.status().preset, "vencord");
});

test("per-state presets mix artwork, replace only their custom image and reset to the baseline", async (t) => {
  const f = fixture(t);
  const stock = {};
  let displayed;
  f.api.register({ isDestroyed: () => false, setImage: (value) => (displayed = value) }, () =>
    f.api.image(stock, { connected: true, speaking: true }),
  );
  f.api.setPreset("equicord");
  await f.api.choose({}, "speaking");
  await f.api.choose({}, "muted");
  const mutedCustom = f.config().trayIcons.muted;
  f.api.setStatePreset("speaking", "vencord");
  assert.equal(f.config().trayIcons.speaking, undefined);
  assert.equal(f.config().trayIcons.muted, mutedCustom);
  assert.match(displayed.filename, /vencord\/tray\.png$/);
  assert.equal(f.api.status().icons.find((icon) => icon.id === "speaking").preset, "vencord");
  f.api.setStatePreset("unread", "discord");
  assert.equal(f.api.image(stock, { unread: true }), stock);
  assert.match(f.api.image(stock, {}).filename, /equicord\/tray\.png$/);
  const loads = f.presetLoads.length;
  f.api.status();
  f.api.image(stock, { connected: true, speaking: true });
  assert.equal(f.presetLoads.length, loads, "per-state presets reuse decoded assets");
  f.api.setPreset("discord");
  assert.equal(displayed, stock);
  assert.equal(Object.keys(f.config().trayIconPresets).length, 0);
  assert.equal(f.api.image(stock, {}), stock);
  assert.equal(f.api.image(stock, { connected: true, muteStates: { selfMute: true } }), stock);
  await f.api.choose({}, "speaking");
  assert.equal(displayed, f.nativeImage);
  assert.equal(f.api.status().icons.find((icon) => icon.id === "speaking").preset, null);
  f.api.reset("speaking");
  assert.equal(displayed, stock);
  assert.equal(f.config().trayIconPresets.speaking, undefined);
  assert.equal(f.config().trayIcons.speaking, undefined);
  assert.equal(f.config().trayIcons.muted, undefined);
  assert.equal(f.api.status().icons.find((icon) => icon.id === "speaking").overridden, false);
});

test("per-state preset validation and failed saves preserve all existing overrides", async (t) => {
  const f = fixture(t);
  for (const state of ["__proto__", "../escape", null, {}])
    assert.throws(() => f.api.setStatePreset(state, "discord"), /Unknown tray icon state/);
  for (const preset of ["__proto__", "../vencord", null, {}])
    assert.throws(() => f.api.setStatePreset("unread", preset), /Unknown tray icon preset/);
  f.api.setStatePreset("unread", "equicord");
  await f.api.choose({}, "unread");
  const previous = JSON.stringify(f.config());
  f.fail();
  assert.throws(() => f.api.setStatePreset("unread", "discord"), /Disk full/);
  assert.equal(JSON.stringify(f.config()), previous);
  assert.throws(() => f.api.reset("unread"), /Disk full/);
  assert.equal(JSON.stringify(f.config()), previous);
  f.config().trayIconPresets.default = "../invalid";
  assert.equal(f.api.status().icons.find((icon) => icon.id === "default").preset, "discord");
});

test("bundled artwork and license bytes match pinned upstream sources for every preset state", () => {
  const files = new Set();
  for (const source of manifest.sources) {
    const license = fs.readFileSync(path.join(presetRoot, source.licenseFile));
    assert.equal(createHash("sha256").update(license).digest("hex"), source.licenseSha256);
    for (const file of source.files) {
      const data = fs.readFileSync(path.join(presetRoot, file.path));
      assert.equal(createHash("sha256").update(data).digest("hex"), file.sha256);
      assert.equal(data.subarray(0, 8).toString("hex"), "89504e470d0a1a0a");
      files.add(file.path);
    }
  }
  for (const preset of Object.values(manifest.presets)) {
    assert.deepEqual(Object.keys(preset.states).sort(), [
      "connected",
      "deafened",
      "default",
      "muted",
      "speaking",
      "unread",
    ]);
    for (const file of Object.values(preset.states)) assert.ok(files.has(file));
  }
});

test("invalid image content and oversized dimensions reject before decoding", (t) => {
  const f = fixture(t);
  fs.writeFileSync(f.source, "not a PNG");
  assert.throws(() => f.api.importImage(f.source), /valid PNG/);
  f.png.writeUInt32BE(16384, 16);
  fs.writeFileSync(f.source, f.png);
  assert.throws(() => f.api.importImage(f.source), /1024/);
});

test("missing custom assets and path traversal fall back to the stock icon", (t) => {
  const f = fixture(t);
  const stock = {};
  f.config().trayIcons = { default: "a".repeat(64) + ".png" };
  assert.equal(f.api.image(stock, {}), stock);
  f.config().trayIcons.default = "../../source.png";
  assert.equal(f.api.image(stock, {}), stock);
});

test("pinned tray patch routes current state and registers live updates", () => {
  const input =
    'function getTrayIcon(){return "stock"}function create(){(atomTray).setToolTip(currentStrings.appName),setContextMenu()}';
  let registered;
  const context = {
    atomTray: { setToolTip() {} },
    trayIcons: { DEFAULT: "default.png", UNREAD: "unread.png" },
    currentStrings: { appName: "Discord" },
    setContextMenu() {},
    getTrayStates: () => ({ unread: true }),
    require: () => ({
      image: (stock, state) => (state.unread ? "custom" : stock),
      register: (tray, getImage, defaults) => (registered = { tray, getImage, defaults }),
    }),
  };
  vm.runInNewContext(patch(input, '/test "root"/tray.cjs') + ";create()", context);
  assert.equal(registered.tray, context.atomTray);
  assert.equal(registered.getImage(), "custom");
  assert.equal(registered.defaults, context.trayIcons);
  for (const source of ["unknown", input + input])
    assert.throws(() => patch(source, "/adapter"), /Unsupported Discord client layout/);
});

test("default previews use Discord's registered paths or native images and return after reset", async (t) => {
  const f = fixture(t);
  const stock = { isEmpty: () => false, toDataURL: () => "data:image/png;base64,stock" };
  f.api.register({ isDestroyed: () => false, setImage() {} }, () => stock, {
    DEFAULT: stock,
    UNREAD: "/discord/tray-unread.png",
  });
  assert.equal(f.api.describe().find((item) => item.id === "default").preview, stock.toDataURL());
  assert.equal(
    f.api.describe().find((item) => item.id === "unread").preview,
    f.nativeImage.toDataURL(),
  );
  await f.api.choose({}, "default");
  assert.equal(
    f.api.describe().find((item) => item.id === "default").preview,
    f.nativeImage.toDataURL(),
  );
  f.api.reset("default");
  assert.equal(f.api.describe().find((item) => item.id === "default").preview, stock.toDataURL());
});

test("default previews are available before Discord creates a tray", (t) => {
  const f = fixture(t);
  assert.equal(
    f.api.describe().every((item) => item.preview === null),
    true,
  );
  f.api.setDefaultDirectory("/installed/desktop/app/images/systemtray/linux");
  assert.equal(
    f.api.describe().filter((item) => item.preview === f.nativeImage.toDataURL() && !item.custom)
      .length,
    6,
  );
});
