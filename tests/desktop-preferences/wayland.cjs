// Run only in an isolated nested compositor, never against the user's session.
const { app, BrowserWindow } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { execFile } = require("node:child_process");
const { promisify } = require("node:util");
const command = promisify(execFile);
const { createController } = require("../../integration/desktop/preferences.cjs");
const output = process.env.DPE_DESKTOP_OUTPUT;
if (!output || !process.env.NIRI_SOCKET?.startsWith(path.join(output, "run") + "/"))
  throw Error("Use a disposable niri socket inside DPE_DESKTOP_OUTPUT/run");
app.setPath("userData", path.join(output, "profile"));
const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const timeout = setTimeout(() => app.exit(1), 60000);
app
  .whenReady()
  .then(async () => {
    const controller = createController({ app }, () => ({}));
    app.on("browser-window-created", (_event, window) => controller.observeWindow(window));
    const window = new BrowserWindow({
      width: 500,
      height: 400,
      autoHideMenuBar: true,
      webPreferences: { backgroundThrottling: false },
    });
    await window.loadURL(
      "data:text/html,<body style='background:%23eee;font:20px sans-serif'>DPE native Wayland controls</body>",
    );
    window.setTitle("DPE Native Wayland controls");
    const captures = [];
    const capture = async (target, name) => {
      await target.webContents.executeJavaScript(
        `document.body.style.outline='${captures.length % 2}px solid transparent';new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`,
      );
      await target.webContents.capturePage();
      await delay(600);
      const windows = JSON.parse(
        (await command("niri", ["msg", "-j", "windows"], { encoding: "utf8" })).stdout,
      );
      const item = windows.find((entry) => entry.title === target.getTitle());
      assert.ok(item, "fixture window must belong to the isolated compositor");
      const filename = path.join(output, name + ".png");
      await command("niri", [
        "msg",
        "action",
        "screenshot-window",
        "--id",
        String(item.id),
        "--path",
        filename,
      ]);
      const deadline = Date.now() + 3000;
      while (!fs.existsSync(filename) && Date.now() < deadline) await delay(50);
      assert.ok(fs.statSync(filename).size > 0);
      captures.push({
        name,
        filename,
        controls: target.getWindowControls(),
        bounds: target.getContentBounds(),
        maximized: target.isMaximized(),
      });
    };
    assert.equal(typeof window.setWindowControls, "function");
    await capture(window, "default");
    for (const action of ["minimize", "maximize", "close"]) {
      await controller.apply({ [action + "Button"]: false });
      assert.equal(window.getWindowControls()[action], false);
      await capture(window, "hide-" + action);
    }
    await controller.apply({ windowControls: false });
    await capture(window, "hide-all");
    const created = new BrowserWindow({
      width: 400,
      height: 320,
      autoHideMenuBar: true,
      webPreferences: { backgroundThrottling: false },
    });
    await created.loadURL("data:text/html,Created%20with%20all%20controls%20disabled");
    created.setTitle("DPE Native created disabled");
    assert.deepEqual(created.getWindowControls(), {
      minimize: false,
      maximize: false,
      close: false,
    });
    await capture(created, "created-disabled");
    created.destroy();
    await controller.apply({ nativeTitlebar: true });
    assert.deepEqual(window.getWindowControls(), { minimize: true, maximize: true, close: true });
    await capture(window, "restored");
    window.maximize();
    await delay(400);
    await controller.apply({ closeButton: false });
    await capture(window, "maximize-request-close-hidden");
    window.unmaximize();
    await delay(400);
    await controller.apply({ nativeTitlebar: true });
    await capture(window, "restored-after-maximize");
    fs.writeFileSync(
      path.join(output, "result.json"),
      JSON.stringify({ passed: true, captures, versions: process.versions }, null, 2),
    );
    window.destroy();
    clearTimeout(timeout);
    app.quit();
  })
  .catch((error) => {
    console.error(error);
    app.exit(1);
  });
