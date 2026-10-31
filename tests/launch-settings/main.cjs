// Exercise the real OpenAsar tabs, sandboxed preload and flags file without an account.
const { app, BrowserWindow, clipboard } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const output = process.env.DPE_SETTINGS_OUTPUT;
if (!output) throw new Error("A private output directory is required");
const root = process.env.DPE_APP_ROOT;
const profile = process.env.DPE_PROFILE_DIR;
// Give Mods a real, disposable installation and keep all downloads disabled in this UI probe.
const fixtureRoot = path.join(output, "app-root");
fs.mkdirSync(path.join(fixtureRoot, "resources"), { recursive: true });
fs.symlinkSync(path.join(root, "integration"), path.join(fixtureRoot, "integration"), "dir");
fs.writeFileSync(
  path.join(fixtureRoot, "resources/build_info.json"),
  JSON.stringify({ releaseChannel: "canary" }),
);
process.env.DPE_APP_ROOT = fixtureRoot;
const modResources = path.join(profile, "app-1.0.1/resources");
fs.mkdirSync(modResources, { recursive: true });
require("original-fs").writeFileSync(
  path.join(modResources, "app.asar"),
  "isolated bootstrap fixture",
);
const { DatabaseSync } = require("node:sqlite");
const database = new DatabaseSync(path.join(profile, "installer.db"));
database.exec("CREATE TABLE key_values (key TEXT PRIMARY KEY, value TEXT)");
database
  .prepare("INSERT INTO key_values VALUES (?, ?)")
  .run("dpe/active/canary", JSON.stringify({ version: "1.0.1" }));
database.close();
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
async function capture(name) {
  await evaluate(
    "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
  );
  fs.writeFileSync(
    path.join(output, name + ".png"),
    (await window.webContents.capturePage()).toPNG(),
  );
}
const headerFits = () =>
  evaluate(`(() => {
    const title = document.querySelector("header > h1").getBoundingClientRect();
    const close = document.getElementById("b_close").getBoundingClientRect();
    const sidebar = document.querySelector(".settings-sidebar").getBoundingClientRect();
    const main = document.querySelector("main").getBoundingClientRect();
    return (title.right <= close.left || close.right <= title.left) &&
      (sidebar.right <= main.left || main.right <= sidebar.left || sidebar.bottom <= main.top);
  })()`);
const change = (id, value, event = "input") =>
  evaluate(`(() => {
  const input = document.getElementById(${JSON.stringify(id)});
  input.value = ${JSON.stringify(value)};
  input.dispatchEvent(new Event(${JSON.stringify(event)}, {bubbles:true}));
})()`);
async function open() {
  window = settingsPage.open("flags");
  window.webContents.on("console-message", (event) => {
    if (event.level === "error") console.error(event.message);
  });
  await until(() => get('$("settings") && !$("settings").hidden && !$("flags-panel").hidden'));
  await until(() => window.isVisible());
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
    const footerWindow = new BrowserWindow({ width: 600, height: 500 });
    try {
      await require(path.join(root, "tests/discord-ui/footer-check.cjs"))(footerWindow);
      checks.push(
        "Discord footer spacing, tooltip anchor, default cursor and keyboard activation pass",
      );
    } finally {
      footerWindow.destroy();
    }
    await open();
    await click("t_settings");
    await capture("general");
    fs.writeFileSync(
      path.join(output, "layout.json"),
      JSON.stringify(
        await evaluate(
          `({innerWidth,innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,offenders:[...document.querySelectorAll("*")].map(x=>({tag:x.tagName,cls:x.className,id:x.id,rect:x.getBoundingClientRect().toJSON()})).filter(x=>x.rect.right>innerWidth||x.rect.bottom>innerHeight)})`,
        ),
        null,
        2,
      ),
    );
    await evaluate('document.body.dataset.dpeTheme = "light"');
    await capture("general-light");
    await evaluate('document.body.dataset.dpeTheme = "dark"');
    await click("t_mods");
    await until(() => get('$("mods-choices").getAttribute("aria-busy") === "false"'));
    assert.equal(await get('$("mods-state").hidden'), true);
    assert.equal(
      await get('$("mods-install-vencord").disabled || $("mods-install-equicord").disabled'),
      false,
    );
    assert.equal(
      await evaluate('document.querySelectorAll("#mods-panel input[type=radio]").length'),
      0,
    );
    assert.equal(await get('Boolean($("mods-remove"))'), false);
    window.close();
    await until(() => window.isDestroyed());
    let setupLaunches = 0;
    window = await require(path.join(root, "integration/updater/mod-settings.cjs")).onboard({
      completeLaunch: () => {
        setupLaunches++;
      },
    });
    await until(() =>
      get('$("welcome-title")?.textContent === "Welcome!" && !$("setup-next").disabled'),
    );
    await until(() => window.isVisible());
    assert.equal(await evaluate('Boolean(document.querySelector(".settings-sidebar"))'), false);
    assert.equal(await evaluate("document.title"), "Welcome to DPE");
    assert.equal(await get('Boolean($("b_maximize"))'), false);
    assert.match(
      await evaluate('document.querySelector(".setup-intro").textContent'),
      /Add plugins and themes to Discord Patched Electron\./,
    );
    const installStyle = await evaluate(`(() => {
      const button=document.getElementById("mods-install-vencord"), style=getComputedStyle(button);
      return {height:button.getBoundingClientRect().height, font:style.fontFamily, size:style.fontSize,
        line:style.lineHeight, weight:style.fontWeight, background:style.backgroundColor,
        icon:button.querySelector("svg").getAttribute("viewBox")};
    })()`);
    assert.equal(installStyle.height, 32);
    assert.match(installStyle.font, /gg sans/);
    assert.equal(installStyle.size, "14px");
    assert.equal(installStyle.line, "18px");
    assert.equal(installStyle.weight, "500");
    assert.equal(installStyle.background, "rgba(151, 151, 159, 0.12)");
    assert.equal(installStyle.icon, "0 0 24 24");
    const installPoint = await evaluate(`(() => {
      const bounds=document.getElementById("mods-install-vencord").getBoundingClientRect();
      return {x:Math.round(bounds.x+bounds.width/2), y:Math.round(bounds.y+bounds.height/2)};
    })()`);
    window.webContents.sendInputEvent({ type: "mouseMove", ...installPoint });
    await delay(200);
    assert.equal(
      await get('getComputedStyle($("mods-install-vencord")).backgroundColor'),
      "rgba(151, 151, 159, 0.16)",
    );
    window.webContents.sendInputEvent({ type: "mouseDown", button: "left", ...installPoint });
    await delay(200);
    assert.equal(
      await get('getComputedStyle($("mods-install-vencord")).backgroundColor'),
      "rgba(151, 151, 159, 0.2)",
    );
    assert.equal(await get('getComputedStyle($("mods-install-vencord")).scale'), "none");
    window.webContents.sendInputEvent({ type: "mouseMove", x: 1, y: 60 });
    window.webContents.sendInputEvent({ type: "mouseUp", button: "left", x: 1, y: 60 });
    if (process.env.DPE_SETTINGS_EXPECT_FONTS === "1") {
      assert.equal(
        await evaluate(`(async () => {
        await Promise.all([400,500,600,700].map(weight => document.fonts.load(weight+' 16px "gg sans"')));
        return [...document.fonts].filter(font=>font.family.includes("gg sans") && font.status==="loaded").length;
      })()`),
        4,
      );
      const debuggerAPI = window.webContents.debugger;
      debuggerAPI.attach("1.3");
      try {
        await debuggerAPI.sendCommand("DOM.enable");
        await debuggerAPI.sendCommand("CSS.enable");
        const { root: documentRoot } = await debuggerAPI.sendCommand("DOM.getDocument");
        const { nodeId } = await debuggerAPI.sendCommand("DOM.querySelector", {
          nodeId: documentRoot.nodeId,
          selector: "#welcome-title",
        });
        const { fonts } = await debuggerAPI.sendCommand("CSS.getPlatformFontsForNode", { nodeId });
        assert.ok(
          fonts.some(
            (font) =>
              font.isCustomFont && /gg\s*sans/i.test(font.familyName) && font.glyphCount > 0,
          ),
        );
        fs.writeFileSync(path.join(output, "welcome-fonts.json"), JSON.stringify(fonts, null, 2));
      } finally {
        debuggerAPI.detach();
      }
    }
    await capture("setup");
    const invalidMod = await evaluate('window.ModSettings.apply("unknown")');
    assert.match(invalidMod.error, /Choose Vencord/);
    await evaluate(`(() => {
      window.__nextProgressFlash = false;
      window.__nextProgressObserver = new MutationObserver(() => {
        if (document.querySelector('.mod-progress:not([hidden])')) window.__nextProgressFlash = true;
      });
      window.__nextProgressObserver.observe(document.body, {subtree:true, childList:true, attributes:true, attributeFilter:['hidden']});
    })()`);
    await click("setup-next");
    await until(() => get('Boolean($("setup-done"))'));
    assert.equal(await evaluate("window.__nextProgressFlash"), false);
    await evaluate("window.__nextProgressObserver.disconnect()");
    const artwork = await evaluate(`(() => {
      const image = document.querySelector('.welcome-artwork > img'), bounds=image.getBoundingClientRect();
      return {loaded:image.complete && image.naturalWidth>0, width:bounds.width, height:bounds.height,
        top:bounds.top, left:bounds.left, viewport:[innerWidth,innerHeight],
        topBlur:getComputedStyle(document.querySelector('.welcome-blur-strong')).backdropFilter,
        footerBlur:getComputedStyle(document.querySelector('.onboarding-actions')).backdropFilter};
    })()`);
    assert.equal(artwork.loaded, true);
    assert.equal(artwork.top, 0);
    assert.equal(artwork.left, 0);
    assert.deepEqual([artwork.width, artwork.height], artwork.viewport);
    assert.match(artwork.topBlur, /blur/);
    assert.match(artwork.footerBlur, /blur/);
    assert.equal(
      await evaluate('document.body.textContent.includes("Closing Settings finishes")'),
      false,
    );
    assert.equal(await get('$("setup-done").textContent.trim()'), "Restart to finish");
    assert.equal(setupLaunches, 0);
    assert.equal(
      JSON.parse(fs.readFileSync(path.join(profile, "dpe-updater/mods.json"))).setupComplete,
      false,
    );
    await capture("setup-ready");
    await click("setup-back");
    await until(() => get('Boolean($("setup-next")) && !$("setup-next").disabled'));
    await click("setup-next");
    await until(() => get('Boolean($("setup-done"))'));
    const welcomeWindow = window;
    await click("setup-configure");
    await until(() => BrowserWindow.getAllWindows().some((item) => item !== welcomeWindow));
    window = BrowserWindow.getAllWindows().find((item) => item !== welcomeWindow);
    await until(() => get('Boolean($("t_settings"))'));
    await until(() => welcomeWindow.isDestroyed());
    assert.equal(window.getParentWindow(), null);
    assert.equal(
      await get('$("onboarding-close-note").textContent.trim()'),
      "Closing settings will launch Discord.",
    );
    await until(() => get('!$("b_restart").disabled'));
    assert.equal(setupLaunches, 0);
    await capture("onboarding-settings");
    await click("b_close");
    await until(() => window.isDestroyed());
    assert.equal(setupLaunches, 1);
    assert.equal(
      JSON.parse(fs.readFileSync(path.join(profile, "dpe-updater/mods.json"))).setupComplete,
      true,
    );
    checks.push(
      "Next never shows install progress; artwork fills the window with progressive top and footer blur",
    );
    checks.push(
      "Configure more closes Welcome, transfers ownership and closing Settings finishes onboarding exactly once",
    );
    await open();
    assert.equal(await get('Boolean($("onboarding-close-note"))'), false);
    await click("t_mods");
    await until(() => get('$("mods-choices").getAttribute("aria-busy") === "false"'));
    assert.equal(await get('Boolean($("setup-next"))'), false);
    assert.equal(await get('$("mods-state").hidden'), true);
    await until(() =>
      evaluate(
        '[...document.querySelectorAll(".project-avatar img, .mod-logo")].every(image=>image.naturalWidth>0)',
      ),
    );
    await capture("mods");
    // Seed an independently packaged real loader to test installed and uninstall UI
    // without executing or downloading a client mod.
    const modSource = path.join(output, "mod-ui-loader");
    const modTarget = path.join(profile, "Vencord/dist/patcher.js");
    fs.mkdirSync(modSource, { recursive: true });
    fs.mkdirSync(path.dirname(modTarget), { recursive: true });
    fs.writeFileSync(modTarget, "// Vencord a1b2c3d\nmodule.exports = {};\n");
    fs.writeFileSync(
      path.join(modSource, "package.json"),
      JSON.stringify({ name: "discord", main: "index.js" }),
    );
    fs.writeFileSync(path.join(modSource, "index.js"), `require(${JSON.stringify(modTarget)})`);
    const modArchive = path.join(output, "mod-ui-loader.asar");
    require("node:child_process").execFileSync(path.join(root, "node_modules/.bin/asar"), [
      "pack",
      modSource,
      modArchive,
    ]);
    const originalFS = require("original-fs");
    originalFS.renameSync(
      path.join(modResources, "app.asar"),
      path.join(modResources, "_app.asar"),
    );
    originalFS.copyFileSync(modArchive, path.join(modResources, "app.asar"));
    await click("t_settings");
    await click("t_mods");
    await until(() => get('$("mods-install-vencord").dataset.installed === "true"'));
    assert.equal(await get('$("mods-install-vencord").disabled'), false);
    assert.equal(await get('$("mods-install-equicord").disabled'), false);
    assert.equal(await get('$("mods-install-vencord").textContent.trim()'), "Uninstall");
    assert.equal(
      await evaluate(`document.querySelector('[data-mod="vencord"] .mod-version').textContent`),
      " (a1b2c3d)",
    );
    assert.match(await get('$("versions").textContent'), /Vencord a1b2c3d/);
    await capture("mods-installed");
    await evaluate('document.getElementById("mods-install-equicord").focus()');
    await click("mods-install-equicord");
    assert.equal(
      await get('$("mods-install-equicord").disabled && $("mods-install-vencord").disabled'),
      true,
    );
    window.close();
    assert.equal(window.isDestroyed(), false);
    await until(() =>
      get('!$("mods-error").hidden && $("mods-choices").getAttribute("aria-busy") === "false"'),
    );
    assert.match(await get('$("mods-error").textContent'), /internet connection/);
    assert.equal(await get('$("mods-install-vencord").dataset.installed'), "true");
    assert.equal(await get('$("mods-install-equicord").disabled'), false);
    assert.equal(await evaluate("document.activeElement.id"), "mods-install-equicord");
    assert.equal(
      originalFS
        .readFileSync(path.join(modResources, "app.asar"))
        .equals(originalFS.readFileSync(modArchive)),
      true,
    );
    await capture("mods-install-error");
    await click("mods-install-vencord");
    await until(() =>
      get(
        '$("mods-choices").getAttribute("aria-busy") === "false" && $("mods-install-vencord").dataset.installed === "false"',
      ),
    );
    assert.equal(await get('$("mods-install-vencord").disabled'), false);
    assert.equal(fs.existsSync(modTarget), true);
    assert.doesNotMatch(await get('$("versions").textContent'), /Vencord/);
    assert.equal(
      originalFS.readFileSync(path.join(modResources, "app.asar"), "utf8"),
      "isolated bootstrap fixture",
    );
    checks.push(
      "Mod rows show installed revision, preserve the mod after failed switching, restore focus, and uninstall without deleting mod data",
    );
    window.close();
    await until(() => window.isDestroyed());
    window = await require(path.join(root, "integration/updater/mod-settings.cjs")).onboard({
      completeLaunch: () => {
        setupLaunches++;
        installedWelcome.close();
      },
    });
    const installedWelcome = window;
    await until(() => get('$("setup-next") && !$("mods-install-vencord").disabled'));
    await click("mods-install-vencord");
    await until(() => get('!$("mods-error").hidden && !$("mods-install-vencord").disabled'));
    assert.match(await get('$("mods-error").textContent'), /internet connection/);
    assert.equal(setupLaunches, 1);
    assert.equal(window.isDestroyed(), false);
    // Supply the already-verified fixture loader after rendering. Apply now
    // verifies that selection without a download, through the real Python/IPC
    // path. Installation must leave the choice page open until explicit Next.
    originalFS.renameSync(
      path.join(modResources, "app.asar"),
      path.join(modResources, "_app.asar"),
    );
    originalFS.copyFileSync(modArchive, path.join(modResources, "app.asar"));
    await click("mods-install-vencord");
    await until(() =>
      get(
        '!$("mods-install-vencord").disabled && $("mods-install-vencord").dataset.installed === "true"',
      ),
    );
    assert.equal(window.isDestroyed(), false);
    assert.equal(setupLaunches, 1);
    assert.equal(await get('Boolean($("setup-next")) && !$("setup-done")'), true);
    assert.equal(
      JSON.parse(fs.readFileSync(path.join(profile, "dpe-updater/mods.json"))).setupComplete,
      false,
    );
    await capture("setup-installed");
    await click("setup-next");
    await until(() => get('Boolean($("setup-done"))'));
    assert.equal(setupLaunches, 1);
    assert.equal(await get('$("setup-done").textContent.trim()'), "Restart to finish");
    await click("setup-done");
    await until(() => window.isDestroyed());
    assert.equal(setupLaunches, 2);
    assert.equal(
      JSON.parse(fs.readFileSync(path.join(profile, "dpe-updater/mods.json"))).setupComplete,
      true,
    );
    originalFS.renameSync(
      path.join(modResources, "_app.asar"),
      path.join(modResources, "app.asar"),
    );
    checks.push(
      "Welcome keeps failed installs retryable and installed mods on the choice page until Next and Restart to finish",
    );
    await open();
    const pagePadding = await evaluate('getComputedStyle(document.querySelector("main")).padding');
    await click("t_theming");
    await until(() => get('Boolean($("theme-css"))'));
    assert.equal(
      await evaluate('getComputedStyle(document.querySelector("main")).padding'),
      pagePadding,
    );
    assert.equal(await get('$("theme-css").value'), "/* preserve me */");
    assert.equal(
      await evaluate(
        `(() => {const field=document.getElementById("theme-css").getBoundingClientRect(),help=document.getElementById("theme-help").getBoundingClientRect(),main=document.querySelector("main").getBoundingClientRect();const style=getComputedStyle(document.querySelector("main")),available=main.bottom-parseFloat(style.paddingBottom)-field.top;return help.bottom<field.top && field.height>=available-2 && field.width>document.getElementById("container").getBoundingClientRect().width*.85;})()`,
      ),
      true,
    );
    assert.deepEqual(window.getSize(), [1100, 800]);
    window.setSize(1400, 900);
    await until(() => evaluate("innerWidth > 1080"));
    assert.equal(
      await evaluate('getComputedStyle(document.querySelector("main")).paddingTop'),
      "64px",
    );
    assert.equal(
      await evaluate('getComputedStyle(document.querySelector("main")).scrollbarGutter'),
      "stable both-edges",
    );
    assert.equal(
      await evaluate(
        `(() => {const panel=document.getElementById("container").getBoundingClientRect(),main=document.querySelector("main").getBoundingClientRect();return Math.abs(panel.width-728)<1 && Math.abs((panel.left+panel.right)-(main.left+main.right))<2;})()`,
      ),
      true,
    );
    await evaluate(
      'document.body.style.setProperty("--dpe-discord-settings-panel-max-width", "740px")',
    );
    assert.equal(await get('Math.round($("container").getBoundingClientRect().width)'), 772);
    await evaluate('document.body.style.removeProperty("--dpe-discord-settings-panel-max-width")');
    window.setSize(720, 840);
    await until(() => evaluate("innerWidth <= 1080"));
    assert.equal(
      await evaluate('getComputedStyle(document.querySelector("main")).paddingTop'),
      "80px",
    );
    await capture("appearance");
    await change("theme-css", "/* saved appearance */");
    await until(() => values.openasar.css === "/* saved appearance */");
    checks.push(
      "First-run mod choice, skip, setup persistence, invalid-choice rejection and local theme editing pass",
    );
    await click("t_settings");
    const detectionToggle = '[aria-label="Automatically detect screen-share audio"]';
    assert.equal(await evaluate(`document.querySelector('${detectionToggle}').checked`), true);
    await evaluate(`document.querySelector('${detectionToggle}').click()`);
    await until(() => values.openasar.audioAutoDetect === false);
    assert.equal(
      require(path.join(root, "integration/audio/portal-detection.cjs")).enabled(),
      false,
    );
    await click("t_flags");
    await click("t_settings");
    assert.equal(await evaluate(`document.querySelector('${detectionToggle}').checked`), false);
    await evaluate(`document.querySelector('${detectionToggle}').click()`);
    await until(() => values.openasar.audioAutoDetect === true);
    await delay(350);
    assert.equal(
      await evaluate(
        `document.querySelector('${detectionToggle}').parentElement.querySelector('.dpe-switch-thumb').style.cssText`,
      ),
      "",
    );
    await evaluate(`document.querySelector('${detectionToggle}').click()`);
    await evaluate(
      "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
    );
    assert.equal(
      await evaluate(
        `Boolean(document.querySelector('${detectionToggle}').parentElement.querySelector('.dpe-switch-thumb').style.width)`,
      ),
      true,
    );
    await evaluate(`document.querySelector('${detectionToggle}').click()`);
    await delay(350);
    assert.equal(
      await evaluate(
        `document.querySelector('${detectionToggle}').parentElement.querySelector('.dpe-switch-thumb').style.cssText`,
      ),
      "",
    );
    await evaluate('document.body.dataset.reducedMotion = "true"');
    await evaluate(`document.querySelector('${detectionToggle}').click()`);
    await evaluate("new Promise(resolve => requestAnimationFrame(resolve))");
    assert.equal(
      await evaluate(
        `document.querySelector('${detectionToggle}').parentElement.querySelector('.dpe-switch-thumb').style.cssText`,
      ),
      "",
    );
    await evaluate(`document.querySelector('${detectionToggle}').click()`);
    await evaluate("delete document.body.dataset.reducedMotion");
    checks.push(
      "Audio auto-detection persists; switch movement retargets, settles and respects reduced motion",
    );
    await evaluate(`(() => {
      document.documentElement.style.setProperty('--dpe-discord-root-font-size','20px');
      for (const [name,value] of Object.entries({'--dpe-discord-switch-width':'60px','--dpe-discord-switch-height':'30px','--dpe-discord-switch-thumb-size':'20px','--dpe-discord-switch-thumb-inset':'5px','--dpe-discord-text-md-size':'18px','--dpe-discord-text-md-line-height':'24px','--control-input-height-md':'44px','--radius-sm':'10px'})) document.body.style.setProperty(name,value);
    })()`);
    assert.equal(await evaluate("getComputedStyle(document.documentElement).fontSize"), "20px");
    assert.deepEqual(
      await evaluate(
        `(() => {const input=document.querySelector('${detectionToggle}'),thumb=input.parentElement.querySelector('.dpe-switch-thumb');return [getComputedStyle(input.parentElement).width,getComputedStyle(thumb).width,getComputedStyle(thumb).insetInlineStart,getComputedStyle(document.querySelector('label[for="setting-audioAutoDetect"]')).fontSize];})()`,
      ),
      ["60px", "20px", "35px", "18px"],
    );
    await evaluate(`document.querySelector('${detectionToggle}').click()`);
    await delay(350);
    assert.equal(
      await evaluate(
        `getComputedStyle(document.querySelector('${detectionToggle}').parentElement.querySelector('.dpe-switch-thumb')).insetInlineStart`,
      ),
      "5px",
    );
    await evaluate(`document.querySelector('${detectionToggle}').click()`);
    await click("t_flags");
    assert.equal(
      await evaluate('getComputedStyle(document.getElementById("display-trigger")).minHeight'),
      "44px",
    );
    assert.equal(
      await evaluate('getComputedStyle(document.getElementById("display-trigger")).borderRadius'),
      "10px",
    );
    await evaluate('document.body.style.setProperty("--dpe-discord-control-height","46px")');
    assert.equal(
      await evaluate('getComputedStyle(document.getElementById("display-trigger")).minHeight'),
      "44px",
    );
    assert.equal(
      await evaluate('getComputedStyle(document.getElementById("save")).minHeight'),
      "46px",
    );
    await evaluate(`(() => {
      document.documentElement.style.removeProperty('--dpe-discord-root-font-size');
      for (const name of ['--dpe-discord-switch-width','--dpe-discord-switch-height','--dpe-discord-switch-thumb-size','--dpe-discord-switch-thumb-inset','--dpe-discord-text-md-size','--dpe-discord-text-md-line-height','--control-input-height-md','--dpe-discord-control-height','--radius-sm']) document.body.style.removeProperty(name);
    })()`);
    checks.push(
      "Native typography, radius, input-height and switch geometry tokens update controls with source-specific precedence",
    );
    await click("t_troubleshooting");
    assert.equal(await get('$("troubleshooting-panel").hidden'), false);
    await capture("troubleshooting");
    assert.equal(await get('$("flags-panel").hidden'), true);
    assert.equal(await get('$("container").hidden'), true);
    for (const name of Object.keys(
      require(path.join(root, "integration/updater/diagnostics.cjs")).pages,
    )) {
      await evaluate(`window.Native.openDiagnostic(${JSON.stringify(name)})`);
      const diagnostic = BrowserWindow.getAllWindows().find((item) => item !== window);
      assert.ok(diagnostic, name);
      assert.equal(diagnostic.webContents.getURL().split("#")[0], `chrome://${name}/`);
      await evaluate(`window.Native.openDiagnostic(${JSON.stringify(name)})`);
      assert.equal(BrowserWindow.getAllWindows().length, 2);
      diagnostic.destroy();
    }
    await assert.rejects(evaluate('window.Native.openDiagnostic("crash")'), /Unknown diagnostic/);
    checks.push(
      "Troubleshooting opens each supported page in Electron, reuses windows and rejects unknown pages",
    );
    await click("t_flags");
    assert.equal(BrowserWindow.getAllWindows().length, 1);
    const displayTrigger = "display-trigger";
    await click(displayTrigger);
    assert.equal(await get('$("display-trigger").getAttribute("aria-expanded")'), "true");
    await evaluate('document.getElementById("display-trigger").focus()');
    window.webContents.sendInputEvent({ type: "keyDown", keyCode: "End" });
    window.webContents.sendInputEvent({ type: "keyUp", keyCode: "End" });
    window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Return" });
    window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Return" });
    await until(() => get('$("display").value === "x11" && !$("flags").disabled'));
    assert.equal(await get('$("display-trigger").textContent.includes("X11")'), true);
    await click(displayTrigger);
    window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Home" });
    window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Home" });
    window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Return" });
    window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Return" });
    await until(() => get('$("display").value === "auto" && !$("flags").disabled'));
    await click(displayTrigger);
    window.webContents.sendInputEvent({ type: "keyDown", keyCode: "Escape" });
    window.webContents.sendInputEvent({ type: "keyUp", keyCode: "Escape" });
    await until(() => get('$("display-trigger").getAttribute("aria-expanded") === "false"'));
    assert.equal(window.isDestroyed(), false);
    await evaluate('document.querySelector("#flags-recovery-help .command-copy").click()');
    await until(() =>
      get(
        '$("flags-recovery-help").querySelector(".command-copy").getAttribute("aria-label") === "Copied command"',
      ),
    );
    assert.equal(
      await clipboard.readText(),
      (await evaluate("window.Native.presentation()")).recoveryCommand,
    );
    checks.push(
      "Native-style combobox keyboard selection, Escape isolation and exact recovery-command clipboard action pass",
    );
    assert.equal(await evaluate("document.title"), "DPE Settings");
    assert.equal(await evaluate('document.querySelector("h1").textContent'), "DPE Settings");
    assert.equal(await get('$("config-github").firstChild.textContent.trim()'), "GitHub");
    assert.equal(
      await get('$("config-github").querySelector(".dpe-icon").getAttribute("aria-hidden")'),
      "true",
    );
    assert.equal(await headerFits(), true);
    assert.equal(
      await evaluate(`(() => {
        const link = document.getElementById("project-menu-trigger").getBoundingClientRect();
        const nav = document.querySelector("nav").getBoundingClientRect();
        return link.bottom <= nav.top;
      })()`),
      true,
    );
    const versionLines = await evaluate(
      '[...document.querySelectorAll("#versions > span:not(.version-feedback):not(.sr-only)")].map(node => node.textContent).join("\\n")',
    );
    const versionBounds = await get('$("versions").getBoundingClientRect().toJSON()');
    await click("versions");
    await until(() =>
      get(
        '$("versions").querySelector("[role=status]").textContent === "Copied version information"',
      ),
    );
    assert.equal(await clipboard.readText(), versionLines);
    assert.deepEqual(await get('$("versions").getBoundingClientRect().toJSON()'), versionBounds);
    assert.equal(await evaluate("getComputedStyle(document.body).userSelect"), "none");
    assert.equal(await get('getComputedStyle($("flags")).userSelect'), "text");
    assert.equal(
      await evaluate('getComputedStyle(document.querySelector(".command code")).userSelect'),
      "text",
    );
    const projectPoint = await get(
      '({x:Math.round($("project-menu-trigger").getBoundingClientRect().left+20),y:Math.round($("project-menu-trigger").getBoundingClientRect().top+20)})',
    );
    for (const expanded of ["true", "false"]) {
      window.webContents.sendInputEvent({
        type: "mouseDown",
        button: "left",
        clickCount: 1,
        ...projectPoint,
      });
      window.webContents.sendInputEvent({
        type: "mouseUp",
        button: "left",
        clickCount: 1,
        ...projectPoint,
      });
      await until(() =>
        get('$("project-menu-trigger").getAttribute("aria-expanded") === "' + expanded + '"'),
      );
    }
    checks.push(
      "Project menu toggles closed on a second pointer click; version footer copies every line without moving; editable text remains selectable",
    );
    await click("project-menu-trigger");
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
      "DPE title and project links above the sidebar; keyboard activation opens the fixed repository through the desktop opener",
    );
    assert.deepEqual(
      await evaluate(
        '[...document.querySelectorAll("[role=tab]")].map(x=>x.lastElementChild.textContent)',
      ),
      ["General", "Appearance", "Mods", "Flags", "Troubleshooting"],
    );
    assert.equal(await evaluate('document.querySelectorAll("textarea").length'), 2);
    assert.equal(await get('$("preset")'), null);
    assert.equal(window.isResizable(), true);
    assert.ok(window.getSize()[0] >= 320);
    assert.equal(await get('$("save").disabled'), true);
    assert.equal(await get('$("advanced-label").textContent'), "Show Advanced Settings");
    assert.equal(await get('$("advanced").open'), false);
    assert.equal(
      await evaluate('getComputedStyle(document.getElementById("advanced-description")).display'),
      "block",
    );
    await evaluate('document.querySelector("#advanced summary").click()');
    await until(() => get('$("advanced-label").textContent === "Hide Advanced Settings"'));
    assert.equal(
      await evaluate('getComputedStyle(document.getElementById("advanced-description")).display'),
      "none",
    );
    await evaluate('document.querySelector("#advanced summary").click()');
    await until(() => get('$("advanced-label").textContent === "Show Advanced Settings"'));
    assert.equal(
      await evaluate(
        'document.querySelectorAll("label .risk-icon").length > 0 && [...document.querySelectorAll("label .risk-icon")].every(icon=>icon.parentNode.firstChild===icon)',
      ),
      true,
    );
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
          `getComputedStyle(document.querySelector('label:is([for="${id}"],[for="${id}-trigger"]) .risk-icon')).color`,
        ),
        "rgb(255, 133, 138)",
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
          `getComputedStyle(document.querySelector('label:is([for="${id}"],[for="${id}-trigger"]) .risk-icon')).color`,
        ),
        "rgb(255, 133, 138)",
      );
    assert.equal(await get('$("flags-recovery-help").closest("details")'), null);
    assert.equal(await get('$("flags-recovery-help").querySelector("hr")'), null);
    assert.equal(
      await get('$("flags-recovery-help").querySelector("pre > code").textContent'),
      (await evaluate("window.Native.presentation()")).recoveryCommand,
    );
    assert.equal(
      await evaluate(`(() => {
        const help = document.getElementById("flags-recovery-help").getBoundingClientRect();
        return help.top >= document.querySelector("#flags-panel .page-heading").getBoundingClientRect().bottom &&
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
    checks.push("One DPE Settings window, five tabs, flags editor and collapsed optional sections");
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
    assert.equal(
      await get('getComputedStyle($("nvidia-vaapi-project")).textDecorationLine'),
      "none",
    );
    await evaluate('document.body.style.setProperty("--text-link", "rgb(12, 123, 234)")');
    assert.equal(
      await get('getComputedStyle($("nvidia-vaapi-project")).color'),
      "rgb(12, 123, 234)",
    );
    await evaluate('document.body.style.removeProperty("--text-link")');
    await evaluate(
      'document.getElementById("nvidia-vaapi-project").scrollIntoView({block:"center"})',
    );
    const linkPoint = await get(
      '({x:Math.round($("nvidia-vaapi-project").getBoundingClientRect().left+5),y:Math.round($("nvidia-vaapi-project").getBoundingClientRect().top+5)})',
    );
    window.webContents.sendInputEvent({ type: "mouseMove", ...linkPoint });
    await until(() =>
      get('getComputedStyle($("nvidia-vaapi-project")).textDecorationLine === "underline"'),
    );
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
      [
        "tab",
        "button",
        "link",
        "checkbox",
        "switch",
        "combobox",
        "textbox",
        "DisclosureTriangle",
      ].includes(node.role?.value),
    );
    assert.ok(controls.length >= 15);
    for (const node of controls) assert.ok(node.name?.value, `Unnamed ${node.role.value}`);
    await evaluate('document.getElementById("t_mods").focus()');
    contents.sendInputEvent({ type: "keyDown", keyCode: "Down" });
    contents.sendInputEvent({ type: "keyUp", keyCode: "Down" });
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
    assert.equal(
      await evaluate("document.documentElement.scrollWidth <= innerWidth"),
      true,
      JSON.stringify(
        await evaluate(
          `Array.from(document.querySelectorAll('body *')).filter(e => {const r=e.getBoundingClientRect();return r.width && (r.left < 0 || r.right > innerWidth)}).map(e=>({tag:e.tagName,id:e.id,class:e.className,x:e.getBoundingClientRect().x,width:e.getBoundingClientRect().width}))`,
        ),
      ),
    );
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
    await evaluate('document.querySelector("main").scrollTo(0,0)');
    await delay(150);
    fs.writeFileSync(path.join(output, "editor.png"), (await contents.capturePage()).toPNG());
    await evaluate('document.getElementById("advanced").open=true');
    await delay(150);
    fs.writeFileSync(path.join(output, "advanced.png"), (await contents.capturePage()).toPNG());
    await evaluate(
      'document.getElementById("advanced").open=false; document.querySelector("main").scrollTo(0,0)',
    );
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
    await evaluate(
      'document.getElementById("advanced").open=false; document.querySelector("main").scrollTo(0,0)',
    );
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
