// Exercise the production page, sandboxed preload and IPC with a fresh profile.
const { app, BrowserWindow } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const output = process.env.DPE_SETTINGS_OUTPUT;
if (!output) throw new Error("A private output directory is required");
const root = process.env.DPE_APP_ROOT;
const profile = process.env.DPE_PROFILE_DIR;
app.setPath("userData", profile);
require(path.join(root, "integration/updater/window.cjs"));
const editor = require(path.join(root, "integration/updater/launch-settings.cjs"));
const settingsPage = require(path.join(root, "integration/updater/config.cjs"));
let values = {
  openasar: { css: "/* preserve me */", themeSync: false, customFlags: "--legacy-ui=kept" },
  unrelated: true,
};
const flagsFile = path.join(process.env.XDG_CONFIG_HOME, "discord-canary-patched-electron.conf");
const savedConfig = async () => (await editor.resolve()).config;
global.settings = {
  get: (key, fallback) => values[key] ?? fallback,
  set: (key, value) => {
    values[key] = value;
  },
  save: () => fs.writeFileSync(path.join(profile, "settings.json"), JSON.stringify(values)),
};
global.settings.save();
const checks = [];
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(predicate) {
  const end = Date.now() + 10000;
  do {
    if (await predicate()) return;
    await delay(50);
  } while (Date.now() < end);
  throw new Error("Timed out waiting for the settings UI");
}
let window;
const evaluate = (code) => window.webContents.executeJavaScript(code);
const get = (expression) =>
  evaluate(`(() => { const $ = (id) => document.getElementById(id); return ${expression}; })()`);
const change = (id, value, event = "input") =>
  evaluate(`(() => {
  const input = document.getElementById(${JSON.stringify(id)});
  input.value = ${JSON.stringify(value)};
  input.dispatchEvent(new Event(${JSON.stringify(event)}, {bubbles:true}));
})()`);
async function open() {
  editor.open();
  window = BrowserWindow.getAllWindows().find((item) => item !== oldWindow);
  await until(() => get('$("settings") && !$("settings").hidden'));
}
let oldWindow;
function finish(error) {
  fs.writeFileSync(
    path.join(output, "settings.json"),
    JSON.stringify(
      { passed: !error, versions: process.versions, checks, error: error?.stack },
      null,
      2,
    ),
  );
  app.exit(error ? 1 : 0);
}
app
  .whenReady()
  .then(async () => {
    settingsPage.open();
    oldWindow = BrowserWindow.getAllWindows()[0];
    await until(() => oldWindow.webContents.executeJavaScript('typeof window.Native === "object"'));
    await open();
    assert.equal(await get('$("preset").value'), "balanced");
    assert.equal(await get('$("decode").checked || $("encode").checked'), false);
    assert.equal(
      await get(
        '$("forceAcceleration").checked || $("graphiteForce").checked || $("compositorBuffers").checked',
      ),
      false,
    );
    assert.equal(await evaluate("typeof window.require"), "undefined");
    checks.push("Balanced defaults, independent codec controls and sandboxed preload");

    await change("preset-flags", "--enable-features=EnableDrDc");
    await evaluate('document.getElementById("settings").requestSubmit()');
    await until(() => get('$("preset-flags").getAttribute("aria-invalid") === "true"'));
    assert.equal(await evaluate("document.activeElement.id"), "preset-flags");
    assert.match(await get('$("error").textContent'), /unsupported on Linux/);
    assert.equal(values.openasar.launchFlags, undefined);
    checks.push("Unsupported flag rejected without saving; error labels and focus reach the field");

    await change(
      "preset-flags",
      "# my preset\n--disable-features=AcceleratedVideoEncoder\n--custom-value=one=two",
    );
    await evaluate('document.getElementById("decode").click()');
    await change("preset", "performance", "change");
    assert.match(await get('$("preset-flags").value'), /disable-background-timer-throttling/);
    assert.equal(await get('$("decode").checked'), false);
    await change("preset", "balanced", "change");
    assert.equal(await get('$("decode").checked'), true);
    assert.match(await get('$("preset-flags").value'), /custom-value=one=two/);
    await evaluate('document.getElementById("settings").requestSubmit()');
    await until(() => get('$("status").textContent.startsWith("Saved.")'));
    assert.equal((await savedConfig()).profiles.balanced.decode, true);
    assert.equal((await savedConfig()).profiles.balanced.encode, false);
    assert.equal(values.openasar.launchFlags, undefined);
    assert.equal(values.openasar.customFlags, undefined);
    assert.match(fs.readFileSync(flagsFile, "utf8"), /--legacy-ui=kept/);
    assert.equal(await get('$("flags-file").textContent'), flagsFile);
    assert.equal(values.unrelated, true);
    assert.match(await get('$("effective").value'), /AcceleratedVideoDecodeLinuxGL/);
    assert.match(await get('$("effective").value'), /--disable-features=AcceleratedVideoEncoder/);
    checks.push("Per-preset edits survive selection, save and real resolver preview");

    const beforeOldPageSave = fs.readFileSync(flagsFile, "utf8");
    await oldWindow.webContents.executeJavaScript(
      'window.Native.set({css:"updated", themeSync:false, launchFlags:{schema:999}, customFlags:"--stale"})',
    );
    await until(() => values.openasar.css === "updated");
    assert.equal(fs.readFileSync(flagsFile, "utf8"), beforeOldPageSave);
    assert.equal(values.openasar.launchFlags, undefined);
    assert.equal(values.openasar.customFlags, undefined);
    assert.equal((await savedConfig()).profiles.balanced.decode, true);
    window.destroy();
    await open();
    assert.match(await get('$("preset-flags").value'), /custom-value=one=two/);
    checks.push(
      "Reopening persists edits; stale OpenAsar settings cannot overwrite launch settings",
    );

    const external =
      fs.readFileSync(flagsFile, "utf8") + "# edited outside app\n--external=value\n";
    fs.writeFileSync(flagsFile, external);
    await change("extra-flags", "--draft=stale");
    await evaluate('document.getElementById("settings").requestSubmit()');
    await until(() => get('$("error").textContent.includes("changed on disk")'));
    assert.equal(fs.readFileSync(flagsFile, "utf8"), external);
    await evaluate('document.getElementById("discard").click()');
    await until(() => get('$("extra-flags").value.includes("--external=value")'));
    assert.match(await get('$("effective").value'), /--external=value/);
    await change("extra-flags", "# edited in app\n--external=replaced\n");
    await evaluate('document.getElementById("settings").requestSubmit()');
    await until(() => get('$("status").textContent.startsWith("Saved.")'));
    assert.ok(
      fs.readFileSync(flagsFile, "utf8").endsWith("# edited in app\n--external=replaced\n"),
    );
    checks.push(
      "External edits block stale saves; reload and Additional flags share the canonical file",
    );

    await evaluate('document.getElementById("wip-details").open = true');
    for (const id of ["forceAcceleration", "graphiteForce", "compositorBuffers"])
      await evaluate(`document.getElementById(${JSON.stringify(id)}).click()`);
    await until(() =>
      get(
        '$("effective").value.includes("--enable-skia-graphite") && $("effective").value.includes("--ignore-gpu-blocklist")',
      ),
    );
    assert.match(await get('$("effective").value'), /VaapiIgnoreDriverChecks/);
    assert.match(
      await get('$("effective").value'),
      /--enable-gpu-memory-buffer-compositor-resources/,
    );
    assert.equal(await get('$("encode").checked'), false);
    assert.equal(await get('$("wip-count").textContent'), "(2 enabled)");
    assert.equal(await get('$("experiment-count").textContent'), "(0 enabled)");
    await change("preset", "performance", "change");
    assert.equal(await get('$("forceAcceleration").checked || $("graphiteForce").checked'), false);
    await change("preset", "balanced", "change");
    assert.equal(await get('$("forceAcceleration").checked && $("graphiteForce").checked'), true);
    await evaluate('document.getElementById("forceAcceleration").click()');
    await until(() => get('!$("effective").value.includes("--ignore-gpu-blocklist")'));
    assert.match(await get('$("effective").value'), /--enable-skia-graphite/);
    await evaluate('document.getElementById("forceAcceleration").click()');
    await evaluate('document.getElementById("settings").requestSubmit()');
    await until(() => get('$("status").textContent.startsWith("Saved.")'));
    assert.deepEqual([...(await savedConfig()).profiles.balanced.experiments].sort(), [
      "compositorBuffers",
      "forceAcceleration",
      "graphiteForce",
    ]);
    window.destroy();
    await open();
    assert.equal(
      await get(
        '$("forceAcceleration").checked && $("graphiteForce").checked && $("compositorBuffers").checked',
      ),
      true,
    );
    await evaluate('document.getElementById("reset-preset").click()');
    await until(() => get('!$("effective").value.includes("--enable-skia-graphite")'));
    assert.equal(
      await get(
        '$("forceAcceleration").checked || $("graphiteForce").checked || $("compositorBuffers").checked',
      ),
      false,
    );
    await evaluate('document.getElementById("discard").click()');
    await until(() => get('$("forceAcceleration").checked && $("graphiteForce").checked'));
    checks.push(
      "Acceleration bypass and WIP controls remain independent, persist per preset, and support reset/discard",
    );

    const contents = window.webContents;
    await evaluate('document.getElementById("experiments-details").open = true');
    await evaluate('document.getElementById("wip-details").open = true');
    contents.debugger.attach("1.3");
    const tree = await contents.debugger.sendCommand("Accessibility.getFullAXTree");
    const controls = tree.nodes.filter((node) =>
      ["button", "checkbox", "combobox", "textbox", "DisclosureTriangle"].includes(
        node.role?.value,
      ),
    );
    assert.ok(controls.length >= 20);
    for (const node of controls) assert.ok(node.name?.value, `Unnamed ${node.role.value}`);
    checks.push("Accessibility tree exposes names and native roles for all editor controls");
    await contents.debugger.sendCommand("Emulation.setDeviceMetricsOverride", {
      width: 320,
      height: 900,
      deviceScaleFactor: 1,
      mobile: false,
    });
    await delay(100);
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
    await evaluate('document.documentElement.dir = "rtl"');
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
    await evaluate('document.documentElement.dir = "ltr"');
    await contents.debugger.sendCommand("Emulation.clearDeviceMetricsOverride");
    contents.setZoomFactor(2);
    await delay(100);
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
    contents.setZoomFactor(1);
    await evaluate('document.getElementById("experiments-details").open = false');
    await evaluate('document.getElementById("preset").focus()');
    contents.sendInputEvent({ type: "keyDown", keyCode: "Tab" });
    contents.sendInputEvent({ type: "keyUp", keyCode: "Tab" });
    await until(() => evaluate('document.activeElement.id === "display"'));
    fs.writeFileSync(path.join(output, "editor.png"), (await contents.capturePage()).toPNG());
    checks.push("320px, RTL and 200% zoom have no horizontal overflow; keyboard navigation works");

    const recoveredFlags = await editor.resolve();
    assert.ok(recoveredFlags.normalEffective.includes("--enable-skia-graphite"));
    process.env.DPE_IGNORE_FLAGS = "1";
    const ignoredFlags = await editor.resolve();
    assert.deepEqual(ignoredFlags.effective, []);
    assert.ok(ignoredFlags.normalEffective.includes("--enable-skia-graphite"));
    checks.push(
      "Recovery ignores saved acceleration and WIP flags while retaining them for repair",
    );

    window.destroy();
    values.openasar.launchFlags = { schema: 999 };
    global.settings.save();
    process.env.DPE_IGNORE_FLAGS = "1";
    process.env.DPE_LAUNCH_ARGS = '["--ignore-flags"]';
    const broken =
      "# >>> OpenAsar launch presets v1\n# presets: {broken}\n--recoverable\n# <<< OpenAsar launch presets v1\ninvalid flag\n";
    fs.writeFileSync(flagsFile, broken);
    await open();
    assert.equal(await get('$("recovery").hidden'), false);
    assert.match(await get('$("warnings").textContent'), /Invalid preset metadata/);
    assert.equal(await get('$("restart").textContent'), "Restart with saved flags");
    assert.match(await get('$("extra-flags").value'), /--recoverable/);
    await evaluate('document.getElementById("settings").requestSubmit()');
    await until(() => get('$("extra-flags").getAttribute("aria-invalid") === "true"'));
    assert.equal(await get('$("restart").disabled'), true);
    assert.equal(fs.readFileSync(flagsFile, "utf8"), broken);
    await change("extra-flags", "# repaired\n--recoverable\n");
    await evaluate('document.getElementById("settings").requestSubmit()');
    await until(() => get('$("status").textContent.startsWith("Saved.")'));
    assert.equal((await editor.resolve()).fileManaged, true);
    assert.equal(await get('$("restart").disabled'), false);
    assert.equal(await get('$("error").hidden'), true);
    const backup = fs
      .readdirSync(path.dirname(flagsFile))
      .find((name) => name.includes(".backup-"));
    assert.equal(fs.readFileSync(path.join(path.dirname(flagsFile), backup), "utf8"), broken);
    let restart,
      quit = false;
    app.relaunch = (options) => {
      restart = options;
    };
    app.quit = () => {
      quit = true;
    };
    await evaluate("window.LaunchSettings.restart()");
    assert.deepEqual(restart, { execPath: process.env.DPE_LAUNCHER, args: [] });
    assert.equal(process.env.DPE_IGNORE_FLAGS, undefined);
    assert.equal(quit, true);
    checks.push("Recovery repairs invalid presets and explicitly returns to normal launch");
    finish();
  })
  .catch(finish);
setTimeout(() => finish(new Error("Settings probe timed out")), 45000);
