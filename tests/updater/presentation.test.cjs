const assert = require("node:assert/strict");
const { test } = require("node:test");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const vm = require("node:vm");
const presentation = require("../../integration/updater/presentation.cjs");
const valid = '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M1 2L3 4Z"></path></svg>';

test("version metadata identifies the client branch and running Linux distribution", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dpe-version-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, "resources"));
  fs.writeFileSync(
    path.join(root, "resources/build_info.json"),
    JSON.stringify({ releaseChannel: "canary", version: "1.0.2357" }),
  );
  fs.writeFileSync(
    path.join(root, "local-build.json"),
    JSON.stringify({ source_commit: "a".repeat(40), source_dirty: true }),
  );
  const versions = presentation.metadata(root, { electron: "46.0.0" });
  assert.equal(versions.discord, "Canary 1.0.2357");
  assert.equal(versions.dpe, "aaaaaaa + changes");
  assert.equal(versions.electron, "46.0.0");
  if (process.platform === "linux") assert.ok(versions.linux.endsWith(os.release()));
  assert.equal(
    presentation.linuxVersion(
      'NAME="Arch Linux"\nPRETTY_NAME="Arch Linux"\nBUILD_ID=rolling',
      "6.18.2",
    ),
    "Arch Linux (rolling) · 6.18.2",
  );
  assert.equal(
    presentation.linuxVersion('PRETTY_NAME="Ubuntu 26.04 LTS"\nVERSION_ID="26.04"', "7.0.0"),
    "Ubuntu 26.04 LTS · 7.0.0",
  );
  assert.equal(
    presentation.linuxVersion("NAME=Fedora\nVERSION_ID=45", "7.0.0"),
    "Fedora 45 · 7.0.0",
  );
  assert.equal(presentation.linuxVersion("", "7.0.0"), "Linux · 7.0.0");
});

test("version metadata follows the active client after an independent Discord update", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dpe-active-version-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const host = path.join(root, "updated-client");
  fs.mkdirSync(path.join(root, "resources"));
  fs.mkdirSync(path.join(host, "resources"), { recursive: true });
  fs.writeFileSync(
    path.join(root, "resources/build_info.json"),
    JSON.stringify({ releaseChannel: "canary", version: "1.0.2300" }),
  );
  const active = path.join(host, "resources/build_info.json");
  fs.writeFileSync(active, JSON.stringify({ releaseChannel: "canary", version: "1.0.2357" }));
  assert.equal(presentation.metadata(root, {}, host).discord, "Canary 1.0.2357");
  fs.writeFileSync(active, JSON.stringify({ releaseChannel: "ptb", version: "1.0.1100" }));
  assert.equal(presentation.metadata(root, {}, host).discord, "PTB 1.0.1100");
  for (const contents of ["invalid JSON", '{"version":false,"releaseChannel":"__proto__"}']) {
    fs.writeFileSync(active, contents);
    assert.equal(presentation.metadata(root, {}, host).discord, "Canary 1.0.2300");
  }
  fs.rmSync(active);
  assert.equal(presentation.metadata(root, {}, host).discord, "Canary 1.0.2300");
});

test("presentation accepts exact SVG geometry and rejects active/foreign SVG content", () => {
  assert.equal(presentation.svg(valid), valid);
  for (const svg of [
    valid.replace("<path ", '<path onclick="alert(1)" '),
    valid.replace('fill="currentColor"', 'fill="url(http://example.invalid)"'),
    "<svg><foreignObject></foreignObject></svg>",
    '<svg><image href="x"></image></svg>',
    "<svg><path></svg>",
    valid + "<script></script>",
  ])
    assert.equal(presentation.svg(svg), undefined);
});

test("runtime icons preserve transparent secondary layers through both adapters", () => {
  const capture = require("../../integration/discord-ui/presentation.cjs");
  const context = {
    document: {
      querySelector: () => null,
      querySelectorAll: () => [],
      documentElement: { classList: { contains: () => false } },
    },
    matchMedia: () => ({ matches: false }),
    ui: {
      runtimes: () => [{}],
      inspect: () => ({
        objects: [],
        choose: (name) =>
          name === "CircleQuestionIcon"
            ? () => ({
                type: "svg",
                props: {
                  viewBox: "0 0 24 24",
                  children: [
                    { type: "circle", props: { cx: 12, cy: 12, r: 10, fill: "transparent" } },
                    { type: "path", props: { d: "M1 2L3 4Z", fill: "#fff" } },
                  ],
                },
              })
            : undefined,
      }),
      unwrap: (value) => value,
      safe: (value, key) => value?.[key],
    },
  };
  const result = vm.runInNewContext(`(${capture.toString()})({iconsOnly:true}, ui)`, context);
  const icon = presentation.sanitize(result).icons.troubleshooting;
  assert.match(icon, /<circle[^>]+fill="transparent"/);
  assert.match(icon, /<path[^>]+fill="currentColor"/);
});

test("theme tokens are bounded without dropping late semantic tokens or accepting CSS injection", () => {
  const tokens = Object.fromEntries(
    Array.from({ length: 2100 }, (_, index) => ["--primary-" + index, "#000"]),
  );
  Object.assign(tokens, {
    "--text-strong": "#eee",
    "--dpe-discord-sidebar": "oklab(0 0 0)",
    "--background-image": "url(https://example.invalid/track)",
    "--background-base-lowest": "red;}body{display:none}",
    "--font-primary": '"gg sans",sans-serif',
    "--unrelated": "private",
  });
  const value = presentation.sanitize({
    tokens,
    classes: ["theme-dark", "theme-midnight", "arbitrary"],
    theme: "light",
    reducedMotion: true,
    icons: { settings: valid, arbitrary: valid, close: "<svg><script></script></svg>" },
  });
  assert.equal(value.tokens["--text-strong"], "#eee");
  assert.equal(value.tokens["--dpe-discord-sidebar"], "oklab(0 0 0)");
  assert.equal(value.tokens["--background-image"], undefined);
  assert.equal(value.tokens["--background-base-lowest"], undefined);
  assert.equal(value.tokens["--unrelated"], undefined);
  assert.deepEqual(value.icons, { settings: valid });
  assert.deepEqual(value.classes, ["theme-dark", "theme-midnight"]);
  assert.equal(value.reducedMotion, true);
});

test("only Discord-owned HTTPS asset fonts and app origins are accepted", () => {
  const font = {
    family: "gg sans",
    weight: "500",
    style: "normal",
    url: "https://canary.discord.com/assets/abc.woff2",
  };
  assert.equal(presentation.sanitize({ fonts: [font] }).fonts.length, 1);
  for (const url of [
    "http://canary.discord.com/assets/a.woff2",
    "https://evil.invalid/assets/a.woff2",
    "https://discord.com/api/a.woff2",
    "https://discord.com/assets/a.woff2?token=secret",
  ])
    assert.equal(presentation.sanitize({ fonts: [{ ...font, url }] }).fonts.length, 0);
  assert.equal(
    presentation.trusted({
      getURL: () => "https://canary.discord.com/channels/@me",
      isDestroyed: () => false,
    }),
    true,
  );
  assert.equal(
    presentation.trusted({
      getURL: () => "https://canary.discord.com.evil.invalid",
      isDestroyed: () => false,
    }),
    false,
  );
});

function fixture(options = {}) {
  const handlers = new Map(),
    sent = [],
    css = [],
    scripts = [],
    copies = [];
  let stopped = false,
    maximized = false,
    shown = false,
    tick;
  let sourceURL = "https://canary.discord.com/channels/@me";
  let snapshot = {
    tokens: { "--background-base-lowest": "#000" },
    theme: "dark",
    classes: ["theme-dark", "theme-midnight"],
    icons: { settings: valid },
    fonts: [],
    fontFamily: '"gg sans"',
  };
  const client = {
    getURL: () => sourceURL,
    getZoomFactor: () => 1.1,
    isDestroyed: () => false,
    executeJavaScript: async () => snapshot,
  };
  const window = new EventEmitter();
  window.webContents = new EventEmitter();
  Object.assign(window.webContents, {
    mainFrame: {},
    ipc: { handle: (name, handler) => handlers.set(name, handler) },
    send: (name, value) => sent.push([name, value]),
    executeJavaScript: async (script) => scripts.push(script),
    insertCSS: async (value) => {
      css.push(value);
      return String(css.length);
    },
    removeInsertedCSS: async () => {},
    setZoomFactor: (value) => {
      window.webContents.zoomFactor = value;
    },
  });
  Object.assign(window, {
    show: () => {
      shown = true;
    },
    isMaximized: () => maximized,
    isDestroyed: () => stopped,
    maximize: () => {
      maximized = true;
      window.emit("maximize");
    },
    unmaximize: () => {
      maximized = false;
      window.emit("unmaximize");
    },
    minimize: () => {},
    close: () => {
      stopped = true;
      window.emit("closed");
    },
  });
  const context = {
    require(name) {
      if (name === "node:path") return path;
      if (name === "node:fs") return fs;
      if (name === "node:os") return os;
      if (name === "./profile.cjs") return { readObject: () => ({}) };
      if (name === "../discord-ui/source.cjs") return { call: () => "capture" };
      if (name === "../discord-ui/presentation.cjs") return () => {};
      if (name === "./native-presentation.cjs")
        return {
          isHelper: () => false,
          acquire: () => options.prewarm,
          remember: async () => {},
          font: async () => "",
        };
      if (name === "../discord-ui/icons.cjs") return () => ({ settings: valid });
      if (name === "./mod-settings.cjs")
        return { installedInfo: () => ({ installed: "vencord", installedVersion: "abcdef1" }) };
      if (name === "electron")
        return {
          BrowserWindow: {
            getAllWindows: () => [{ webContents: client }],
            fromWebContents: () => ({ isMaximized: () => false }),
          },
          clipboard: { writeText: (value) => copies.push(value) },
          net: {
            fetch: () => {
              throw Error("No font fetch expected");
            },
          },
        };
      throw Error(name);
    },
    process: { env: { DPE_PACKAGE_NAME: "discord-canary" }, versions: { electron: "46.0.0" } },
    global: {},
    module: { exports: {} },
    URL,
    Buffer,
    setTimeout,
    clearTimeout,
    setInterval: (callback) => {
      tick = callback;
      return { unref() {} };
    },
    clearInterval: () => {
      stopped = true;
    },
  };
  vm.runInNewContext(fs.readFileSync("integration/updater/presentation.cjs", "utf8"), context);
  const bridge = context.module.exports.attach(window, client, options);
  const invoke = (name, ...args) =>
    handlers.get(name)(
      { sender: window.webContents, senderFrame: window.webContents.mainFrame },
      ...args,
    );
  return {
    window,
    bridge,
    handlers,
    invoke,
    sent,
    css,
    scripts,
    copies,
    get stopped() {
      return stopped;
    },
    get shown() {
      return shown;
    },
    tick: () => tick(),
    set snapshot(value) {
      snapshot = value;
    },
    set sourceURL(value) {
      sourceURL = value;
    },
  };
}

test("settings follows live theme, constrains IPC and releases its polling lifecycle", async () => {
  const f = fixture();
  f.window.webContents.emit("did-finish-load");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(f.shown, true);
  assert.equal(f.window.webContents.zoomFactor, 1.1);
  assert.match(f.css.at(-1), /--background-base-lowest:#000/);
  assert.match(f.scripts.at(-1), /theme-midnight/);
  f.snapshot = {
    tokens: { "--background-base-lowest": "#fff" },
    theme: "light",
    classes: ["theme-light"],
    fonts: [],
    icons: {},
  };
  await f.bridge.sync();
  assert.match(f.css.at(-1), /--background-base-lowest:#fff/);
  assert.equal((await f.invoke("dpe-presentation")).theme, "light");
  assert.throws(
    () => f.handlers.get("dpe-presentation")({ sender: {}, senderFrame: {} }),
    /local settings/,
  );
  assert.throws(() => f.invoke("dpe-window-action", "run-command"), /Unknown window/);
  f.invoke("dpe-window-action", "maximize");
  assert.equal((await f.invoke("dpe-presentation")).maximized, true);
  await f.invoke("dpe-copy-recovery", "arbitrary text is ignored");
  assert.deepEqual(f.copies, ["discord-canary --ignore-flags"]);
  const count = f.css.length;
  f.sourceURL = "https://example.invalid/";
  await f.bridge.sync();
  assert.equal(f.css.length, count);
  f.window.emit("closed");
  assert.equal(f.stopped, true);
});

test("Welcome captures native presentation before showing, then freezes only its icons", async () => {
  const f = fixture({ mode: "welcome", waitForReady: true, lockIcons: true });
  const initial = await f.invoke("dpe-presentation");
  assert.equal(initial.mode, "welcome");
  assert.equal(initial.icons.settings, valid);
  const rendered = f.invoke("dpe-config-ready");
  assert.equal(f.shown, false);
  const nativeIcon = valid.replace("M1 2", "M2 3");
  f.snapshot = { tokens: { "--background-base-lowest": "#000" }, icons: { settings: nativeIcon } };
  f.window.webContents.emit("did-finish-load");
  await rendered;
  assert.equal(f.shown, true);
  assert.ok(f.css.some((css) => css.includes("--background-base-lowest:#000")));
  assert.equal((await f.invoke("dpe-presentation")).icons.settings, nativeIcon);
  f.snapshot = {
    tokens: { "--background-base-lowest": "red" },
    icons: { settings: valid.replace("M1 2", "M4 5") },
  };
  await f.bridge.sync();
  assert.equal((await f.invoke("dpe-presentation")).icons.settings, nativeIcon);
  assert.ok(f.css.some((css) => css.includes("--background-base-lowest:red")));
  f.window.emit("closed");
});

test("version clipboard uses actual displayed metadata rather than renderer input", async () => {
  assert.equal(
    presentation.versionText(
      { dpe: "1.2", discord: "Canary 1.0.1", linux: "Arch Linux · 7.3" },
      { installed: "equicord", installedVersion: "abc1234" },
    ),
    "DPE 1.2\nDiscord Canary 1.0.1\nArch Linux · 7.3\nEquicord abc1234",
  );
  const f = fixture();
  const result = await f.invoke("dpe-copy-versions", "arbitrary injected text");
  assert.match(result, /DPE Development/);
  assert.match(result, /Vencord abcdef1/);
  assert.doesNotMatch(result, /arbitrary injected/);
  assert.equal(f.copies.at(-1), result);
  f.window.emit("closed");
});

test("closing while fonts settle cancels the ready gate without a disposed-frame error", async () => {
  const f = fixture({ mode: "welcome", waitForReady: true });
  f.window.webContents.emit("did-finish-load");
  await new Promise((resolve) => setImmediate(resolve));
  f.window.webContents.executeJavaScript = async () => {
    f.window.close();
    throw Error("Render frame was disposed before the request completed");
  };
  assert.equal(await f.invoke("dpe-config-ready"), false);
  assert.equal(f.shown, false);
});

test("first Welcome waits for isolated native assets before showing without a running client", async () => {
  let resolve;
  const prewarm = new Promise((done) => {
    resolve = done;
  });
  const f = fixture({ mode: "welcome", waitForReady: true, lockIcons: true, prewarm });
  f.sourceURL = "https://example.invalid/";
  const rendered = f.invoke("dpe-config-ready");
  f.window.webContents.emit("did-finish-load");
  await new Promise((done) => setImmediate(done));
  assert.equal(f.shown, false);
  const nativeIcon = valid.replace("M1 2", "M7 8");
  resolve({
    zoom: 1.2,
    snapshot: {
      tokens: { "--background-base-lowest": "#123456" },
      icons: { settings: nativeIcon },
      theme: "dark",
      classes: ["theme-dark"],
      fonts: [],
    },
  });
  await rendered;
  assert.equal(f.shown, true);
  assert.equal(f.window.webContents.zoomFactor, 1.2);
  assert.equal((await f.invoke("dpe-presentation")).icons.settings, nativeIcon);
  assert.ok(f.css.some((value) => value.includes("--background-base-lowest:#123456")));
  f.window.emit("closed");
});
