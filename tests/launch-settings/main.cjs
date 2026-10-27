// Exercise the real OpenAsar tabs, sandboxed preload and flags file without an account.
const { app, BrowserWindow } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const output = process.env.DPE_SETTINGS_OUTPUT;
if (!output) throw new Error("A private output directory is required");
const root = process.env.DPE_APP_ROOT;
const profile = process.env.DPE_PROFILE_DIR;
app.setPath("userData", profile);
app.on("window-all-closed", () => {});
require(path.join(root, "integration/updater/window.cjs"));
const editor = require(path.join(root, "integration/updater/launch-settings.cjs"));
const settingsPage = require(path.join(root, "integration/updater/config.cjs"));
const values = {
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
const click = (id) => evaluate(`document.getElementById(${JSON.stringify(id)}).click()`);
const change = (id, value, event = "input") =>
  evaluate(`(() => {
  const input = document.getElementById(${JSON.stringify(id)});
  input.value = ${JSON.stringify(value)};
  input.dispatchEvent(new Event(${JSON.stringify(event)}, {bubbles:true}));
})()`);
async function open() {
  window = settingsPage.open("flags");
  await until(() => get('$("settings") && !$("settings").hidden && !$("flags-panel").hidden'));
}
async function save() {
  await until(() =>
    get('$("save").getAttribute("aria-disabled") !== "true" && !$("flags").disabled'),
  );
  await evaluate('document.getElementById("settings").requestSubmit()');
  await until(() => get('$("status").textContent.startsWith("Saved.")')).catch(async (failure) => {
    throw new Error(failure.message + ": " + (await get('$("error").textContent')));
  });
}
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
    await open();
    assert.equal(BrowserWindow.getAllWindows().length, 1);
    assert.deepEqual(
      await evaluate('[...document.querySelectorAll("[role=tab]")].map(x=>x.textContent)'),
      ["Settings", "Theming", "Flags"],
    );
    assert.equal(await evaluate('document.querySelectorAll("textarea").length'), 1);
    assert.deepEqual(await get('[...$("preset").options].map(x=>x.textContent)'), [
      "Default",
      "Custom",
    ]);
    assert.equal(await get('$("breaking").open'), false);
    assert.equal(
      await get(
        '$("decode").checked || $("encode").checked || $("forceAcceleration").checked || $("graphiteForce").checked',
      ),
      false,
    );
    assert.equal(await evaluate("typeof window.require"), "undefined");
    assert.match(await get('$("flags").value'), /--openh264-library-path=/);
    checks.push("One OpenAsar window, three tabs, one flags box and collapsed risky options");

    await evaluate('document.getElementById("breaking").open=true');
    await click("decode");
    await until(() => get('!$("flags").disabled'));
    assert.equal(await get('$("display").value'), "auto");
    await save();
    assert.equal((await savedConfig()).profiles.balanced.replaceDefaults, undefined);
    assert.doesNotMatch((await savedConfig()).profiles.balanced.flags, /--openh264-library-path/);
    await click("decode");
    await until(() => get('!$("flags").disabled'));
    await save();
    checks.push("Convenience switches preserve automatic display and codec paths");

    const initial = await get('$("flags").value');
    await change("flags", initial + "\n--enable-features=EnableDrDc");
    await evaluate('document.getElementById("settings").requestSubmit()');
    await until(() => get('$("flags").getAttribute("aria-invalid") === "true"'));
    assert.equal(await evaluate("document.activeElement.id"), "flags");
    assert.match(await get('$("error").textContent'), /unsupported on Linux/);
    assert.equal(await get('$("b_restart").disabled'), true);
    checks.push("Invalid flags announce an error, focus the field and block restart");

    await change("flags", initial + "\n--custom-value=one=two");
    await evaluate('document.getElementById("breaking").open=true');
    await click("decode");
    assert.match(await get('$("flags").value'), /AcceleratedVideoDecodeLinuxGL/);
    await change("preset", "performance", "change");
    await until(() => get('!$("flags").disabled'));
    assert.equal(await get('$("decode").checked'), false);
    assert.doesNotMatch(await get('$("flags").value'), /disable-background/);
    await change("preset", "balanced", "change");
    await until(() => get('!$("flags").disabled'));
    assert.equal(await get('$("decode").checked'), true);
    assert.match(await get('$("flags").value'), /custom-value=one=two/);
    await click("t_settings");
    assert.equal(
      await evaluate(
        '[...document.querySelectorAll("#container input")].every(input => input.getAttribute("aria-label"))',
      ),
      true,
    );
    await click("t_flags");
    assert.match(await get('$("flags").value'), /custom-value=one=two/);
    assert.equal(BrowserWindow.getAllWindows().length, 1);
    await save();
    assert.equal((await savedConfig()).profiles.balanced.replaceDefaults, true);
    assert.equal(values.openasar.launchFlags, undefined);
    assert.equal(values.openasar.customFlags, undefined);
    assert.equal(values.unrelated, true);
    assert.equal(await get('$("flags").title'), flagsFile);
    assert.match(fs.readFileSync(flagsFile, "utf8"), /legacy-ui=kept/);
    checks.push("Manual edits and video controls persist per preset and survive tab changes");

    const before = fs.readFileSync(flagsFile, "utf8");
    await evaluate(
      'window.Native.set({css:"updated", themeSync:false, launchFlags:{schema:999}, customFlags:"--stale"})',
    );
    await until(() => values.openasar.css === "updated");
    assert.equal(fs.readFileSync(flagsFile, "utf8"), before);
    window.destroy();
    await open();
    assert.match(await get('$("flags").value'), /custom-value=one=two/);
    checks.push("Reopening retains flags; ordinary OpenAsar settings cannot overwrite them");

    const external =
      fs.readFileSync(flagsFile, "utf8") + "# edited outside app\n--external=value\n";
    fs.writeFileSync(flagsFile, external);
    await change("flags", (await get('$("flags").value')) + "\n--draft=stale");
    await evaluate('document.getElementById("settings").requestSubmit()');
    await until(() => get('$("error").textContent.includes("changed on disk")'));
    assert.equal(fs.readFileSync(flagsFile, "utf8"), external);
    await click("discard");
    await until(() => get('$("flags").value.includes("--external=value")'));
    await change(
      "flags",
      (await get('$("flags").value')).replace("--external=value", "--external=replaced"),
    );
    await save();
    assert.ok((await editor.resolve()).effective.includes("--external=replaced"));
    assert.ok(!(await editor.resolve()).effective.includes("--external=value"));
    assert.match((await savedConfig()).profiles.performance.flags, /--external=value/);
    checks.push("External edits block stale saves; one-box edits preserve inactive presets");

    await evaluate('document.getElementById("breaking").open=true');
    for (const id of ["forceAcceleration", "graphiteForce", "driverWorkarounds"]) await click(id);
    assert.match(await get('$("flags").value'), /--ignore-gpu-blocklist/);
    assert.match(await get('$("flags").value'), /--enable-skia-graphite/);
    assert.equal(await get('$("encode").checked'), false);
    await click("encode");
    assert.match(await get('$("flags").value'), /AcceleratedVideoEncoder/);
    await change(
      "flags",
      (await get('$("flags").value')) + "\n--disable-features=AcceleratedVideoEncoder",
    );
    assert.equal(await get('$("encode").checked'), false);
    await save();
    await click("reset-preset");
    await until(() => get('!$("flags").disabled'));
    assert.equal(await get('$("forceAcceleration").checked || $("graphiteForce").checked'), false);
    await click("discard");
    await until(() => get('$("forceAcceleration").checked && $("graphiteForce").checked'));
    checks.push("Risky controls update one box; manual overrides, reset and reload stay in sync");

    const contents = window.webContents;
    contents.debugger.attach("1.3");
    const tree = await contents.debugger.sendCommand("Accessibility.getFullAXTree");
    const controls = tree.nodes.filter((node) =>
      ["tab", "button", "checkbox", "combobox", "textbox", "DisclosureTriangle"].includes(
        node.role?.value,
      ),
    );
    assert.ok(controls.length >= 15);
    for (const node of controls) assert.ok(node.name?.value, `Unnamed ${node.role.value}`);
    await evaluate('document.getElementById("t_theming").focus()');
    contents.sendInputEvent({ type: "keyDown", keyCode: "Right" });
    contents.sendInputEvent({ type: "keyUp", keyCode: "Right" });
    await until(() => evaluate('document.activeElement.id === "t_flags"'));
    assert.equal(await get('$("t_flags").getAttribute("aria-selected")'), "true");
    await contents.debugger.sendCommand("Emulation.setDeviceMetricsOverride", {
      width: 320,
      height: 900,
      deviceScaleFactor: 1,
      mobile: false,
    });
    await delay(100);
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
    await evaluate('document.documentElement.dir="rtl"');
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
    await evaluate('document.documentElement.dir="ltr"');
    await contents.debugger.sendCommand("Emulation.clearDeviceMetricsOverride");
    contents.setZoomFactor(2);
    await delay(100);
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
    contents.setZoomFactor(1);
    await evaluate('document.getElementById("breaking").open=false');
    await click("reset-preset");
    await until(() => get('!$("flags").disabled'));
    await evaluate("window.scrollTo(0,0)");
    await delay(150);
    fs.writeFileSync(path.join(output, "editor.png"), (await contents.capturePage()).toPNG());
    await evaluate('document.getElementById("breaking").open=true');
    await delay(150);
    fs.writeFileSync(path.join(output, "breaking.png"), (await contents.capturePage()).toPNG());
    await click("discard");
    await until(() => get('$("graphiteForce").checked'));
    checks.push("Named native controls, keyboard tabs, 320px, RTL and 200% zoom pass");

    process.env.DPE_IGNORE_FLAGS = "1";
    assert.deepEqual((await editor.resolve()).effective, []);
    window.destroy();
    process.env.DPE_LAUNCH_ARGS = '["--ignore-flags"]';
    const broken =
      "# >>> OpenAsar launch presets v1\n# presets: {broken}\n--recoverable\n# <<< OpenAsar launch presets v1\ninvalid flag\n";
    fs.writeFileSync(flagsFile, broken);
    await open();
    assert.equal(await get('$("recovery").hidden'), false);
    assert.match(await get('$("flags").value'), /--recoverable/);
    await evaluate('document.getElementById("settings").requestSubmit()');
    await until(() => get('$("flags").getAttribute("aria-invalid") === "true"'));
    assert.equal(fs.readFileSync(flagsFile, "utf8"), broken);
    await change("flags", "# repaired\n--recoverable\n");
    await save();
    assert.equal(await get('$("b_restart").disabled'), false);
    assert.equal(await get('$("error").hidden'), true);
    assert.ok(
      fs
        .readdirSync(path.dirname(flagsFile))
        .filter((name) => name.includes(".backup-"))
        .some(
          (name) => fs.readFileSync(path.join(path.dirname(flagsFile), name), "utf8") === broken,
        ),
    );
    let restart,
      quit = false;
    app.relaunch = (options) => {
      restart = options;
    };
    app.quit = () => {
      quit = true;
    };
    await click("b_restart");
    await until(() => quit);
    assert.deepEqual(restart, { execPath: process.env.DPE_LAUNCHER, args: [] });
    assert.equal(process.env.DPE_IGNORE_FLAGS, undefined);
    checks.push(
      "Recovery repairs a broken file, backs it up and restarts without the recovery switch",
    );
    finish();
  })
  .catch(finish);
setTimeout(() => finish(new Error("Settings probe timed out")), 45000);
