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
// Exercise the IPC and a real opener child process without launching the user's browser.
const openerDir = path.join(output, "desktop-opener");
const openerLog = path.join(output, "external-links.jsonl");
fs.mkdirSync(openerDir);
fs.writeFileSync(
  path.join(openerDir, "xdg-open"),
  `#!/usr/bin/node
const fs = require("node:fs");
fs.appendFileSync(${JSON.stringify(openerLog)}, JSON.stringify({args: process.argv.slice(2), preload: process.env.LD_PRELOAD, libraries: process.env.LD_LIBRARY_PATH, node: process.env.ELECTRON_RUN_AS_NODE, options: process.env.NODE_OPTIONS}) + "\\n");
process.exit(0);
`,
  { mode: 0o755 },
);
const originalPath = process.env.PATH;
process.env.PATH = openerDir + path.delimiter + originalPath;
const externalCalls = () =>
  fs.existsSync(openerLog)
    ? fs
        .readFileSync(openerLog, "utf8")
        .trim()
        .split("\n")
        .map((line) => JSON.parse(line))
    : [];
const externalURLs = () => externalCalls().map((call) => call.args[0]);
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
const headerFits = () =>
  evaluate(`(() => {
    const title = document.querySelector("header > h1").getBoundingClientRect();
    const github = document.getElementById("config-github").getBoundingClientRect();
    const tabs = document.querySelector("nav").getBoundingClientRect();
    return (title.right <= github.left || github.right <= title.left) &&
      tabs.top >= Math.max(title.bottom, github.bottom);
  })()`);
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
  await until(() => get('!$("save").disabled && !$("flags").disabled'));
  await evaluate('document.getElementById("settings").requestSubmit()');
  await until(() => get('$("status").textContent.startsWith("Saved.")')).catch(async (failure) => {
    throw new Error(failure.message + ": " + (await get('$("error").textContent')));
  });
  assert.equal(await get('$("save").disabled'), true);
}
function finish(error) {
  process.env.PATH = originalPath;
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
    assert.equal(await evaluate("document.title"), "DPE OpenAsar Config");
    assert.equal(await evaluate('document.querySelector("h1").textContent'), "DPE OpenAsar Config");
    assert.equal(await get('$("config-github").textContent.trim()'), "GitHub");
    assert.equal(
      await get('$("config-github").querySelector("svg").getAttribute("aria-hidden")'),
      "true",
    );
    assert.equal(await headerFits(), true);
    assert.equal(
      await evaluate(`(() => {
        const link = document.getElementById("config-github").getBoundingClientRect();
        const header = document.querySelector("header").getBoundingClientRect();
        return link.right === header.right && link.top === header.top;
      })()`),
      true,
    );
    await evaluate('document.getElementById("config-github").focus()');
    window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Return" });
    window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Return" });
    await until(() => externalURLs().length > 0);
    assert.deepEqual(externalURLs(), [
      "https://github.com/discord-patched-electron/discord-patched-electron",
    ]);
    assert.equal(window.webContents.getURL(), "dpe-splash://app/config.html");
    assert.equal(BrowserWindow.getAllWindows().length, 1);
    checks.push(
      "DPE title and top-right GitHub link; keyboard activation opens the fixed repository through the desktop opener",
    );
    assert.deepEqual(
      await evaluate('[...document.querySelectorAll("[role=tab]")].map(x=>x.textContent)'),
      ["Settings", "Theming", "Flags"],
    );
    assert.equal(await evaluate('document.querySelectorAll("textarea").length'), 2);
    assert.equal(await get('$("preset")'), null);
    assert.deepEqual(window.getSize(), [560, 760]);
    assert.equal(await get('$("save").disabled'), true);
    assert.equal(
      await evaluate('document.querySelector("#advanced summary").textContent'),
      "Advanced",
    );
    assert.equal(await get('$("advanced").open'), false);
    assert.equal(await get('$("settings").querySelectorAll("details").length'), 1);
    for (const id of [
      "decode",
      "encode",
      "nvidiaVaapi",
      "forceAcceleration",
      "softwareDecode",
      "softwareRaster",
    ])
      assert.equal(await get(`$("${id}").closest("details").id`), "advanced");
    for (const id of ["backend", "driverWorkarounds", "graphiteForce"])
      assert.equal(await get(`$("${id}").closest("details").id`), "advanced");
    for (const id of ["encode", "graphiteForce"]) {
      assert.match(await get(`$("${id}-help").textContent`), /Known to crash!/);
      assert.equal(
        await evaluate(
          `getComputedStyle(document.querySelector('label[for="${id}"] .risk-icon')).color`,
        ),
        "rgb(255, 133, 133)",
      );
    }
    for (const id of [
      "backend",
      "decode",
      "nvidiaVaapi",
      "forceAcceleration",
      "softwareDecode",
      "softwareRaster",
      "driverWorkarounds",
    ])
      assert.notEqual(
        await evaluate(
          `getComputedStyle(document.querySelector('label[for="${id}"] .risk-icon')).color`,
        ),
        "rgb(255, 133, 133)",
      );
    assert.equal(await get('$("flags-recovery-help").closest("details")'), null);
    assert.equal(await get('$("flags-recovery-help").querySelector("hr")'), null);
    assert.equal(
      await get('$("flags-recovery-help").querySelector("pre > code").textContent'),
      "discord-canary-prerelease-patched-electron-bin --ignore-flags",
    );
    assert.equal(
      await evaluate(`(() => {
        const help = document.getElementById("flags-recovery-help").getBoundingClientRect();
        return help.top >= document.querySelector("nav").getBoundingClientRect().bottom &&
          help.bottom <= document.querySelector("#settings .flag-row").getBoundingClientRect().top;
      })()`),
      true,
    );
    checks.push(
      "One Advanced group contains every experimental control; only reproduced crashes are red; recovery command is in a code block above the form",
    );
    assert.equal(
      await get(
        '$("decode").checked || $("encode").checked || $("forceAcceleration").checked || $("graphiteForce").checked',
      ),
      false,
    );
    assert.equal(await evaluate("typeof window.require"), "undefined");
    assert.equal(await get('$("flags").value'), "--legacy-ui=kept");
    assert.ok((await editor.resolve()).effective.includes("--enable-libopenh264"));
    checks.push("One OpenAsar window, three tabs, flags editor and collapsed optional sections");
    assert.equal(await get('$("essential-section").hidden'), false);
    assert.equal(await get('$("essential-flags").disabled'), true);
    assert.equal(
      await evaluate('getComputedStyle(document.getElementById("essential-flags")).opacity'),
      "0.5",
    );
    const essentialBounds = await evaluate(
      'JSON.stringify(document.getElementById("essential-flags").getBoundingClientRect())',
    );
    const originalConfig = JSON.stringify(await savedConfig());
    await click("edit-essential");
    assert.equal(await get('$("essential-flags").disabled'), false);
    assert.equal(await get('$("save").disabled'), true);
    assert.equal(
      await evaluate(
        'JSON.stringify(document.getElementById("essential-flags").getBoundingClientRect())',
      ),
      essentialBounds,
    );
    assert.equal(JSON.stringify(await savedConfig()), originalConfig);
    const essentials = await get('$("essential-flags").value');
    assert.match(essentials, /--openh264-library-path=\{OPENH264_LIBRARY_PATH\}/);
    assert.doesNotMatch(essentials, /\/tmp\/|--autoplay-policy/);
    assert.match(await get('$("essential-help").textContent'), /break startup\. Variables:/);
    assert.equal(
      await evaluate(
        'document.querySelector("#essential-section .risk-icon").getAttribute("aria-label")',
      ),
      "May break startup",
    );
    await change("essential-flags", essentials + "\n--temporary");
    assert.equal(await get('$("save").disabled'), false);
    await change("essential-flags", essentials);
    assert.equal(await get('$("save").disabled'), true);
    const originalFlags = await get('$("flags").value');
    await change("flags", originalFlags + "\n--temporary");
    assert.equal(await get('$("save").disabled'), false);
    await change("flags", originalFlags);
    assert.equal(await get('$("save").disabled'), true);
    await evaluate('document.getElementById("essential-section").scrollIntoView()');
    await delay(100);
    fs.writeFileSync(
      path.join(output, "essentials.png"),
      (await window.webContents.capturePage()).toPNG(),
    );
    assert.equal(
      await evaluate(
        'document.querySelector(".essential-toggle").getBoundingClientRect().left < document.getElementById("reset-flags").getBoundingClientRect().left',
      ),
      true,
    );
    await change("essential-flags", "--path={UNKNOWN_VARIABLE}");
    await evaluate('document.getElementById("settings").requestSubmit()');
    await until(() => get('$("essential-flags").getAttribute("aria-invalid") === "true"'));
    assert.equal(await evaluate("document.activeElement.id"), "essential-flags");
    await change("essential-flags", "--class={DESKTOP_ID}");
    await save();
    assert.ok(!(await editor.resolve()).effective.includes("--enable-libopenh264"));
    window.destroy();
    await open();
    await click("edit-essential");
    assert.equal(await get('$("essential-flags").value'), "--class={DESKTOP_ID}");
    await change("essential-flags", essentials);
    await save();
    await click("edit-essential");
    assert.equal(await get('$("essential-flags").disabled'), true);
    const unchanged = fs.readFileSync(flagsFile, "utf8");
    const unchangedTime = fs.statSync(flagsFile, { bigint: true }).mtimeNs;
    await evaluate('document.getElementById("settings").requestSubmit()');
    await delay(100);
    assert.equal(fs.readFileSync(flagsFile, "utf8"), unchanged);
    assert.equal(fs.statSync(flagsFile, { bigint: true }).mtimeNs, unchangedTime);
    checks.push("Essential flags stay visible; editing, variables, validation and reload pass");
    checks.push(
      "Save is disabled on load, after saving and after reverting edits; unchanged submit does not write",
    );

    await evaluate('document.getElementById("advanced").open=true');
    assert.doesNotMatch(await get('$("advanced").querySelector(".hint").textContent'), /Breaking:/);
    assert.match(await get('$("nvidiaVaapi-help").textContent'), /Requires nvidia-vaapi-driver/);
    await click("nvidia-vaapi-project");
    await until(() => externalURLs().length === 2);
    assert.equal(externalURLs()[1], "https://github.com/elFarto/nvidia-vaapi-driver");
    assert.deepEqual(
      externalCalls().map((call) => call.args.length),
      [1, 1],
    );
    await evaluate('window.openConfigLink("unknown")');
    assert.equal(await get('$("external-link-error").hidden'), false);
    assert.match(await get('$("external-link-error").textContent'), /Unknown project link/);
    await click("config-github");
    await until(() => externalURLs().length === 3);
    assert.equal(await get('$("external-link-error").hidden'), true);
    assert.doesNotMatch(await get('$("softwareDecode-help").textContent'), /OpenH264/);
    await assert.rejects(
      () => evaluate('window.Native.openLink("https://example.com")'),
      /Unknown project link/,
    );
    assert.equal(externalURLs().length, 3);
    checks.push(
      "Native shell handoff receives exact URLs; IPC errors are visible and arbitrary URLs are rejected",
    );

    assert.equal(window.webContents.getURL(), "dpe-splash://app/config.html");
    await click("nvidiaVaapi");
    await until(() => get('!$("flags").disabled'));
    assert.equal(
      await get('$("decode").checked || $("encode").checked || $("forceAcceleration").checked'),
      false,
    );
    assert.match(await get('$("flags").value'), /VaapiOnNvidiaGPUs/);
    await save();
    await click("discard");
    await until(() => get('!$("settings").inert && $("nvidiaVaapi").checked'));
    await click("nvidiaVaapi");
    await until(() => get('!$("flags").disabled'));
    await save();
    checks.push(
      "NVIDIA VA-API saves independently of encoding and bypasses, reloads, and links to its required driver through the desktop opener",
    );
    await change("display", "x11", "change");
    await until(() => get('!$("flags").disabled'));
    assert.equal(await get('$("save").disabled'), false);
    await change("display", "auto", "change");
    await until(() => get('!$("flags").disabled'));
    assert.equal(await get('$("save").disabled'), true);
    await click("decode");
    await until(() => get('!$("flags").disabled'));
    await click("decode");
    await until(() => get('!$("flags").disabled'));
    assert.equal(await get('$("save").disabled'), true);
    await click("decode");
    await until(() => get('!$("flags").disabled'));
    assert.equal(await get('$("essential-flags").disabled'), true);
    assert.equal(await get('$("display").value'), "auto");
    await save();
    assert.equal((await savedConfig()).profiles.balanced.replaceDefaults, undefined);
    assert.doesNotMatch((await savedConfig()).profiles.balanced.flags, /--openh264-library-path/);
    await click("decode");
    await until(() => get('!$("flags").disabled'));
    await save();
    checks.push("Convenience switches preserve automatic display and codec paths");

    await click("forceAcceleration");
    await until(() => get('!$("flags").disabled'));
    await click("softwareDecode");
    await until(() => get('!$("flags").disabled'));
    assert.equal(await get('$("decode").checked || $("forceAcceleration").checked'), false);
    assert.equal(await get('$("softwareDecode").checked'), true);
    await click("softwareRaster");
    await until(() => get('!$("flags").disabled'));
    assert.match(await get('$("flags").value'), /--disable-gpu-rasterization/);
    await save();
    await click("discard");
    await until(() =>
      get('!$("settings").inert && $("softwareDecode").checked && $("softwareRaster").checked'),
    );
    await click("forceAcceleration");
    await until(() => get('!$("flags").disabled'));
    assert.equal(await get('$("softwareDecode").checked || $("softwareRaster").checked'), false);
    assert.equal(await get('$("decode").checked && $("forceAcceleration").checked'), true);
    await click("forceAcceleration");
    await until(() => get('!$("flags").disabled'));
    await save();
    checks.push(
      "Software fallbacks persist and conflict correctly with forced decoding and rasterization",
    );

    const initial = await get('$("flags").value');
    await change("flags", initial + "\n--enable-features=EnableDrDc");
    await evaluate('document.getElementById("settings").requestSubmit()');
    await until(() => get('$("flags").getAttribute("aria-invalid") === "true"'));
    assert.equal(await evaluate("document.activeElement.id"), "flags");
    assert.match(await get('$("error").textContent'), /unsupported on Linux/);
    assert.equal(await get('$("b_restart").disabled'), true);
    checks.push("Invalid flags announce an error, focus the field and block restart");

    await change("flags", initial + "\n--custom-value=one=two");
    await evaluate('document.getElementById("advanced").open=true');
    await click("decode");
    await until(() => get('!$("flags").disabled'));
    assert.match(await get('$("flags").value'), /AcceleratedVideoDecodeLinuxGL/);
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
    assert.equal((await savedConfig()).profiles.balanced.replaceDefaults, undefined);
    assert.doesNotMatch(
      await get('$("flags").value'),
      /--openh264-library-path|--enable-libopenh264|--class=/,
    );
    assert.ok((await editor.resolve()).effective.includes("--enable-libopenh264"));
    assert.equal(values.openasar.launchFlags, undefined);
    assert.equal(values.openasar.customFlags, undefined);
    assert.equal(values.unrelated, true);
    assert.equal(await get('$("flags").title'), flagsFile);
    assert.match(fs.readFileSync(flagsFile, "utf8"), /legacy-ui=kept/);
    checks.push("Manual edits and video controls persist and survive tab changes");

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
    assert.equal(await get('$("save").disabled'), true);
    await change(
      "flags",
      (await get('$("flags").value')).replace("--external=value", "--external=replaced"),
    );
    await save();
    assert.ok((await editor.resolve()).effective.includes("--external=replaced"));
    assert.ok(!(await editor.resolve()).effective.includes("--external=value"));
    assert.match((await savedConfig()).profiles.performance.flags, /--external=value/);
    checks.push("External edits block stale saves; one-box edits preserve inactive presets");

    await evaluate('document.getElementById("advanced").open=true');
    for (const id of ["forceAcceleration", "graphiteForce", "driverWorkarounds"]) {
      await click(id);
      await until(() => get('!$("flags").disabled'));
    }
    assert.match(await get('$("flags").value'), /--ignore-gpu-blocklist/);
    assert.match(await get('$("flags").value'), /--enable-skia-graphite/);
    assert.equal(await get('$("encode").checked'), false);
    await click("encode");
    await until(() => get('!$("flags").disabled'));
    assert.match(await get('$("flags").value'), /AcceleratedVideoEncoder/);
    await change(
      "flags",
      (await get('$("flags").value')) + "\n--disable-features=AcceleratedVideoEncoder",
    );
    assert.equal(await get('$("encode").checked'), false);
    await save();
    await click("reset-flags");
    await until(() => get('!$("flags").disabled'));
    assert.equal(await get('$("forceAcceleration").checked || $("graphiteForce").checked'), false);
    await click("discard");
    await until(() => get('$("forceAcceleration").checked && $("graphiteForce").checked'));
    checks.push("Risky controls update one box; manual overrides, reset and reload stay in sync");

    await click("edit-essential");
    const contents = window.webContents;
    contents.debugger.attach("1.3");
    const tree = await contents.debugger.sendCommand("Accessibility.getFullAXTree");
    const controls = tree.nodes.filter((node) =>
      ["tab", "button", "link", "checkbox", "combobox", "textbox", "DisclosureTriangle"].includes(
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
    assert.equal(await headerFits(), true);
    await evaluate('document.documentElement.dir="rtl"');
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
    assert.equal(await headerFits(), true);
    await evaluate('document.documentElement.dir="ltr"');
    await evaluate(`(() => {
      document.querySelector('.essential-toggle').lastChild.textContent = 'Wesentliche Startoptionen bearbeiten';
      document.getElementById('reset-flags').textContent = 'Zurücksetzen';
      document.getElementById('discard').textContent = 'Neu laden';
      document.getElementById('save').textContent = 'Änderungen speichern';
    })()`);
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
    await evaluate(`(() => {
      document.querySelector('.essential-toggle').lastChild.textContent = 'Edit essential flags';
      document.getElementById('reset-flags').textContent = 'Reset';
      document.getElementById('discard').textContent = 'Reload';
      document.getElementById('save').textContent = 'Save';
    })()`);
    await contents.debugger.sendCommand("Emulation.clearDeviceMetricsOverride");
    contents.setZoomFactor(2);
    await delay(100);
    assert.equal(await evaluate("document.documentElement.scrollWidth <= innerWidth"), true);
    assert.equal(await headerFits(), true);
    contents.setZoomFactor(1);
    await click("edit-essential");
    await evaluate('document.getElementById("advanced").open=false');
    await click("reset-flags");
    await until(() => get('!$("flags").disabled'));
    await evaluate("window.scrollTo(0,0)");
    await delay(150);
    fs.writeFileSync(path.join(output, "editor.png"), (await contents.capturePage()).toPNG());
    await evaluate('document.getElementById("advanced").open=true');
    await delay(150);
    fs.writeFileSync(path.join(output, "advanced.png"), (await contents.capturePage()).toPNG());
    await evaluate('document.getElementById("advanced").open=false; window.scrollTo(0,0)');
    await delay(100);
    fs.writeFileSync(path.join(output, "recovery.png"), (await contents.capturePage()).toPNG());
    await click("discard");
    await until(() => get('$("graphiteForce").checked'));
    checks.push(
      "Named native controls, keyboard tabs, 320px, longer labels, RTL and 200% zoom pass",
    );

    window.destroy();
    const legacy = await editor.resolve();
    legacy.config.active = "performance";
    legacy.config.profiles.performance.flags = "--legacy-custom=kept";
    legacy.config.profiles.balanced.flags = "--archived={UNKNOWN_VARIABLE}";
    await editor.resolve(legacy.config, { save: true, fileRevision: legacy.fileRevision });
    await open();
    assert.equal(await get('$("preset")'), null);
    assert.match(await get('$("flags").value'), /--legacy-custom=kept/);
    await change("flags", "--legacy-custom=updated");
    await save();
    assert.equal((await savedConfig()).active, "performance");
    assert.equal((await savedConfig()).profiles.balanced.flags, "--archived={UNKNOWN_VARIABLE}");
    await change("display", "x11", "change");
    await until(() => get('!$("flags").disabled'));
    await click("reset-flags");
    await until(() => get('!$("flags").disabled'));
    assert.equal(await get('$("display").value'), "auto");
    assert.equal(await get('$("flags").value'), "");
    assert.equal(await get('$("essential-flags").value'), essentials);
    await save();
    await evaluate('document.getElementById("advanced").open=false; window.scrollTo(0,0)');
    await delay(100);
    fs.writeFileSync(
      path.join(output, "defaults.png"),
      (await window.webContents.capturePage()).toPNG(),
    );
    checks.push(
      "Removing presets keeps the selected legacy setup editable and preserves inactive data; Reset restores launch defaults",
    );

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
