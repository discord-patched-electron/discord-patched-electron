// Account-free real-click regression. Run in Xvfb with DPE_LINK_OUTPUT and
// DPE_LINK_CORE (the exact extracted desktop bundle); optionally DPE_LINK_BASELINE=1.
const { app, BrowserWindow, session } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const output = process.env.DPE_LINK_OUTPUT;
if (!output || !process.env.DPE_LINK_CORE) throw Error("Supply fresh output and desktop core");
fs.mkdirSync(output, { recursive: true });
app.setPath("userData", path.join(output, "profile"));
const opener = path.join(output, "bin");
fs.mkdirSync(opener);
const record = path.join(output, "opened.jsonl");
fs.writeFileSync(
  path.join(opener, "xdg-open"),
  `#!/bin/sh\nprintf '%s\\n' "$1" >> '${record.replaceAll("'", "'\\''")}'\n`,
  { mode: 0o755 },
);
process.env.PATH = opener + path.delimiter + process.env.PATH;
const baseline = process.env.DPE_LINK_BASELINE === "1";
const timer = setTimeout(() => app.exit(1), 45000);
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const read = () =>
  fs.existsSync(record) ? fs.readFileSync(record, "utf8").trim().split("\n").filter(Boolean) : [];
app.on("window-all-closed", () => {});
app
  .whenReady()
  .then(async () => {
    const original = fs.readFileSync(process.env.DPE_LINK_CORE, "utf8");
    const source = baseline
      ? original
      : require("../../integration/updater/patches.cjs").desktop(
          original,
          path.resolve(__dirname, "../../integration/desktop/safe-storage.cjs"),
        );
    // Execute the actual pinned security module, not a permissive opener stand-in.
    const securityStart = source.indexOf("exports.shouldOpenExternalUrl=");
    const securityEnd = source.indexOf("},6317(", securityStart);
    assert.ok(securityStart >= 0 && securityEnd > securityStart);
    const security = {};
    vm.runInNewContext(
      `(function(exports,__webpack_require__,require){${source.slice(securityStart, securityEnd)}})`,
      { URL },
    )(security, () => require("electron"), require);
    await session.defaultSession.protocol.handle(
      "https",
      () =>
        new Response('<a id="link">test link</a>', { headers: { "Content-Type": "text/html" } }),
    );
    const cases = [];
    for (const kind of ["main", "popout"]) {
      const window = new BrowserWindow({
        show: false,
        webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false },
      });
      const contents = window.webContents;
      const context = {
        mainWindow: window,
        mainWebContents: contents,
        popoutWindow: window,
        exports: { WEBAPP_ENDPOINT: "https://canary.discord.com" },
        webappEndpoint: "https://canary.discord.com",
        securityUtils_1: security,
        require,
        frameView_1: { handleFrameViewWindowOpen: () => null },
        DISCORD_NAMESPACE: "DISCORD_",
        getSanitizedPath: (url) => new URL(url).pathname,
        popoutWindows: { openOrFocusWindow: () => ({ action: "deny" }) },
      };
      const prefix = kind === "main" ? "mainWindow.webContents" : "popoutWindow.webContents";
      const navStart = source.indexOf(prefix + '.on("will-navigate",');
      const navEnd = source.indexOf(
        kind === "main"
          ? ',mainWindow.webContents.on("context-menu"'
          : ",popoutWindow.webContents.setWindowOpenHandler",
        navStart,
      );
      assert.ok(navStart >= 0 && navEnd > navStart);
      vm.runInNewContext(source.slice(navStart, navEnd), context);
      const openPrefix =
        kind === "main"
          ? "mainWebContents.setWindowOpenHandler"
          : "popoutWindow.webContents.setWindowOpenHandler";
      const openStart = source.indexOf(openPrefix);
      const openEnd = source.indexOf(
        kind === "main"
          ? ',mainWindow.webContents.on("did-fail-load"'
          : ',popoutWindow.once("closed"',
        openStart,
      );
      assert.ok(openStart >= 0 && openEnd > openStart);
      vm.runInNewContext(source.slice(openStart, openEnd), context);
      await window.loadURL("https://canary.discord.com/channels/@me");
      for (const target of ["_self", "_blank", "named-link"]) {
        for (const relative of [false, true]) {
          const url = `https://example.com/${kind}/${target}/${relative}?q=a%26b#fragment`;
          const count = read().length;
          await contents.executeJavaScript(
            `link.href=${JSON.stringify(relative ? url.slice(6) : url)};link.target=${JSON.stringify(target)};link.click()`,
            true,
          );
          const expected = !baseline || target !== "_self";
          const until = Date.now() + (expected ? 2000 : 150);
          while (Date.now() < until && read().length === count) await delay(20);
          assert.equal(
            read().length,
            count + Number(expected),
            `${kind} ${target} relative=${relative}`,
          );
          if (expected) assert.equal(read().at(-1), url);
          assert.equal(contents.getURL(), "https://canary.discord.com/channels/@me");
          cases.push({ kind, target, relative, dispatched: expected });
        }
      }
      const count = read().length;
      await contents.executeJavaScript(
        'link.href="/channels/internal";link.target="_self";link.click()',
        true,
      );
      for (let i = 0; i < 100 && contents.getURL().endsWith("/@me"); i++) await delay(20);
      assert.equal(contents.getURL(), "https://canary.discord.com/channels/internal");
      assert.equal(read().length, count);
      window.destroy();
    }
    fs.writeFileSync(
      path.join(output, "report.json"),
      JSON.stringify(
        {
          passed: true,
          baseline,
          versions: process.versions,
          core: process.env.DPE_LINK_CORE,
          cases,
        },
        null,
        2,
      ),
    );
    clearTimeout(timer);
    app.exit(0);
  })
  .catch((error) => {
    console.error(error);
    app.exit(1);
  });
