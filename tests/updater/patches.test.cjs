const assert = require("node:assert/strict");
const { test } = require("node:test");
const { desktop, bootstrap } = require("../../integration/updater/patches.cjs");
const desktopExec = require("../../integration/updater/desktop-exec.cjs");

const migration = "event.returnValue=injectedUpdater?.getUpdater()!=null";
const modulePath = "return null!=newUpdater?newUpdater.committedModulePaths.get(name)??null:null";
const storage =
  "electron.ipcMain.on(SAFE_STORAGE_IS_ENCRYPTION_AVAILABLE,event=>{event.returnValue=null!=electron.safeStorage&&electron.safeStorage.isEncryptionAvailable()})," +
  'electron.ipcMain.on(SAFE_STORAGE_ENCRYPT_STRING,(event,plainText)=>{null!=plainText?event.returnValue=electron.safeStorage.encryptString(plainText).toString("base64"):event.returnValue=null}),' +
  'electron.ipcMain.on(SAFE_STORAGE_DECRYPT_STRING,(event,encrypted)=>{if(null!=encrypted)try{event.returnValue=electron.safeStorage.decryptString(Buffer.from(encrypted,"base64"))}catch{event.returnValue=null}else event.returnValue=null})';
const fixture = [migration, modulePath, storage].join(";\n");

test("desktop transforms disable native migration, resolve modules and install safe storage", () => {
  const output = desktop(fixture, '/portable "app"/storage.cjs');
  assert.ok(output.includes("event.returnValue=!0"));
  assert.ok(output.includes("injectedModuleUpdater?.getModulePath(name)??null"));
  assert.ok(output.includes('require("/portable \\"app\\"/storage.cjs").install(electron,'));
  assert.ok(!output.includes("electron.safeStorage.encryptString"));
});

test("unknown or duplicated desktop patch sites fail before activation", () => {
  for (const site of [migration, modulePath, storage]) {
    assert.throws(
      () => desktop(fixture.replace(site, "unknown"), "/adapter"),
      /Unsupported Discord client layout/,
    );
    assert.throws(
      () => desktop(fixture + ";" + site, "/adapter"),
      /Unsupported Discord client layout/,
    );
  }
});

test("autostart quotes portable paths and escapes desktop field codes", () => {
  assert.equal(desktopExec("/portable app/launch.sh"), '"/portable app/launch.sh"');
  assert.equal(desktopExec('/a%u/"$\\/launch.sh'), String.raw`"/a%%u/\\"\\$\\\\/launch.sh"`);
  assert.equal(desktopExec("/a`/launch.sh"), '"/a\\\\`/launch.sh"');
});

test("bootstrap transforms restore settings through the renderer adapter and keep the package launcher", () => {
  const entry =
    "process.resourcesPath = join(__dirname, '..');\nglobal.oaConfig = settings.get('openasar', {});";
  const source =
    "before\n  app.on('browser-window-created', customize);\ncustomization();\n  desktopCore = require('discord_desktop_core');\nafter";
  const transformed = bootstrap(source, "bootstrap.js", "/desktop-exec.cjs");
  assert.ok(!transformed.includes("customization()"));
  assert.ok(transformed.includes("renderer.cjs"));
  assert.ok(bootstrap(entry, "index.js").includes("...settings.get('openasar', {})"));
  assert.ok(transformed.endsWith("  desktopCore = require('discord_desktop_core');\nafter"));
  assert.ok(bootstrap(entry, "index.js").includes("autoupdate: false"));
  const autostart = bootstrap(
    "const exec = app.getPath('exe');\n`Exec=${exec}`",
    "autoStart.js",
    "/desktop-exec.cjs",
  );
  assert.ok(autostart.includes("process.env.DPE_LAUNCHER"));
  assert.ok(autostart.includes('require("/desktop-exec.cjs")(exec)'));
});

test("missing or repeated bootstrap patch sites fail closed", () => {
  for (const [filename, source] of [
    ["index.js", "global.oaConfig = settings.get('openasar', {});"],
    ["autoStart.js", "const exec = app.getPath('exe');\n`Exec=${exec}`"],
    [
      "bootstrap.js",
      "  app.on('browser-window-created', customize);\n  desktopCore = require('discord_desktop_core');",
    ],
  ]) {
    assert.throws(() => bootstrap("unknown", filename), /Unsupported pinned OpenAsar/);
    assert.throws(() => bootstrap(source + source, filename), /Unsupported pinned OpenAsar/);
  }
});
