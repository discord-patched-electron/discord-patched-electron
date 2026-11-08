// Account-free integration test of the real desktop-core popout module. Run on
// a disposable display with DPE_POPOUT_OUTPUT and DPE_POPOUT_CORE (bundle.js).
const electron = require("electron");
const { app, BrowserWindow } = electron;
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const { createServer } = require("node:http");
const { once } = require("node:events");
const { execFileSync } = require("node:child_process");
const output = process.env.DPE_POPOUT_OUTPUT;
if (!output || !process.env.DPE_POPOUT_CORE)
  throw Error("Use a fresh fixture output and core bundle");
fs.mkdirSync(output, { recursive: true });
app.setPath("userData", path.join(output, "profile"));
process.env.DPE_PROFILE_DIR = path.join(output, "profile");
app.on("window-all-closed", () => {});
const timer = setTimeout(() => app.exit(1), 30000);
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const report = { passed: false, versions: process.versions, cases: [], realCallVerified: false };

function loadPopouts() {
  const source = require("../../integration/updater/patches.cjs").desktop(
    fs.readFileSync(process.env.DPE_POPOUT_CORE, "utf8"),
    path.resolve(__dirname, "../../integration/desktop/safe-storage.cjs"),
  );
  const marker = source.indexOf("exports.openOrFocusWindow=");
  const start = source.lastIndexOf("(__unused_rspack_module,exports,__webpack_require__){", marker);
  const end = source.indexOf("},", source.indexOf("newWindowEvent=()=>{}", marker));
  assert.ok(start >= 0 && end > start);
  const body = source.slice(start + source.slice(start).indexOf("{") + 1, end);
  const dependencies = {
    electron_1: electron,
    path_1: path,
    securityUtils_1: {
      checkUrlOriginMatches: (url, endpoint) => new URL(url).origin === endpoint,
      saferShellOpenExternal: () => {
        throw Error("Unexpected external URL");
      },
    },
    appFeatures_1: { getFeatures: () => ({ declareSupported() {} }) },
    constants_1: {},
    overlay: {},
  };
  const modules = new Map();
  for (const [name, value] of Object.entries(dependencies)) {
    const match = body.match(
      new RegExp(`${name}=(?:__importDefault\\()?__webpack_require__\\((\\d+)\\)`),
    );
    if (match) modules.set(Number(match[1]), value);
  }
  const exports = {};
  vm.runInNewContext(`(function(exports,__webpack_require__,require){${body}})`, {
    process,
    console,
  })(
    exports,
    (id) => {
      assert.ok(modules.has(id), `Unexpected dependency ${id}`);
      return modules.get(id);
    },
    require,
  );
  return exports;
}

app
  .whenReady()
  .then(async () => {
    require("../../integration/desktop/preferences.cjs").install(electron, {
      getConfig: () => JSON.parse(process.env.DPE_POPOUT_PREFS || "{}"),
    });
    const popouts = loadPopouts();
    console.log("popout module loaded");
    const server = createServer((req, res) => {
      res.setHeader("Content-Type", "text/html");
      res.end(
        req.url === "/popout"
          ? '<div id="app-mount"></div><script>window.onload=()=>window.opener?.postMessage({loaded:true},window.origin)</script>'
          : `<canvas id="source" width="640" height="360"></canvas><script>
        const context=source.getContext('2d');let frame=0;
        setInterval(()=>{context.fillStyle=++frame%2?'red':'blue';context.fillRect(0,0,640,360)},40);
        window.stream=source.captureStream(25);
        window.addEventListener('message',async event=>{
          if(event.origin!==window.origin||!event.data.loaded)return;
          const child=event.source,video=child.document.createElement('video');
          video.muted=true;video.autoplay=true;video.srcObject=window.stream;
          child.document.querySelector('#app-mount').append(video);
          await video.play();child.receivedFrames=0;
          function tick(){child.receivedFrames++;video.requestVideoFrameCallback(tick)}
          video.requestVideoFrameCallback(tick);
        });</script>`,
      );
    });
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    const url = `http://127.0.0.1:${server.address().port}`;
    const parent = new BrowserWindow({
      title: "DPE popout fixture parent",
      width: 900,
      height: 650,
      webPreferences: { sandbox: false, nodeIntegration: false, contextIsolation: true },
    });
    parent.webContents.setWindowOpenHandler(({ url: target, frameName, features }) =>
      new URL(target).origin === url &&
      new URL(target).pathname === "/popout" &&
      frameName.startsWith("DISCORD_")
        ? popouts.openOrFocusWindow(target, frameName, features, parent)
        : { action: "deny" },
    );
    parent.webContents.on("did-create-window", (child, { options, frameName }) => {
      child.windowKey = frameName;
      popouts.setupPopout(child, frameName, options, url, parent.webContents);
    });
    console.log("loading parent", url);
    await parent.loadURL(url);
    console.log("parent loaded");
    for (const key of [
      "DISCORD_CHANNEL_CALL_POPOUT",
      "DISCORD_CALL_TILE_POPOUT_123_stream:456",
      "DISCORD_CALL_TILE_POPOUT_123_stream:456",
      "DISCORD_RTC_DEBUG_POPOUT",
    ]) {
      const video = key !== "DISCORD_RTC_DEBUG_POPOUT";
      console.log("opening", key);
      const created = once(parent.webContents, "did-create-window");
      await parent.webContents.executeJavaScript(
        `window.child=window.open('/popout',${JSON.stringify(key)},'menubar=no,toolbar=no,location=no,directories=no,width=854,height=480,');Boolean(child)`,
      );
      const [child] = await created;
      console.log("created", key);
      child.setTitle(key);
      await delay(900);
      const result = await child.webContents.executeJavaScript(
        `({opener:!!window.opener,frames:window.receivedFrames||0,node:typeof process,video:!!document.querySelector('video')})`,
      );
      assert.equal(result.opener, true);
      assert.equal(result.node, "undefined");
      assert.ok(result.video && result.frames > 2, JSON.stringify(result));
      assert.equal(child.getParentWindow(), video ? parent : null);
      if (video) {
        const [width, height] = child.getSize();
        const expected = require("../../integration/desktop/popout.cjs").mediaSize({
          ...require("electron").screen.getDisplayMatching(parent.getBounds()),
          scaleFactor: 1,
        });
        assert.ok(
          Math.abs(width - expected.width) <= 2 && Math.abs(height - expected.height) <= 2,
          `${width}x${height}`,
        );
        if (process.env.NIRI_SOCKET) {
          const windows = JSON.parse(
            execFileSync("niri", ["msg", "-j", "windows"], { encoding: "utf8" }),
          );
          assert.equal(windows.find((window) => window.title === key)?.is_floating, true);
          result.floating = true;
        }
        assert.equal(child.isResizable(), true);
        assert.deepEqual(child.getMaximumSize(), [0, 0]);
        child.setSize(640, 360);
        await delay(150);
        assert.deepEqual(child.getSize(), [640, 360]);
        const preferences = require("../../integration/desktop/preferences.cjs");
        await preferences.apply({ minimumWindowSize: true });
        assert.deepEqual(child.getMinimumSize(), [320, 180]);
        assert.deepEqual(child.getSize(), [640, 360]);
        await preferences.apply(JSON.parse(process.env.DPE_POPOUT_PREFS || "{}"));
        child.setAlwaysOnTop(true);
        child.setAlwaysOnTop(false);
        parent.focus();
        await delay(200);
        assert.ok(await child.webContents.executeJavaScript(`receivedFrames>${result.frames}`));
      }
      report.cases.push({ key, ...result, size: child.getSize() });
      child.destroy();
      assert.equal(popouts.getWindow(key), undefined);
      await delay(250);
    }
    parent.destroy();
    server.close();
    report.passed = true;
  })
  .catch((error) => {
    report.error = error.stack;
  })
  .finally(() => {
    clearTimeout(timer);
    fs.writeFileSync(path.join(output, "report.json"), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
    app.exit(report.passed ? 0 : 1);
  });
