// Real Electron API and CSS probe with a disposable profile and no Discord login.
const { app, BrowserWindow } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { createController } = require("../../integration/desktop/preferences.cjs");
const output = process.env.DPE_DESKTOP_OUTPUT;
if (!output) throw Error("DPE_DESKTOP_OUTPUT must point to a disposable directory");
fs.mkdirSync(path.join(output, "profile"), { recursive: true });
app.setPath("userData", path.join(output, "profile"));
app.on("window-all-closed", () => {});
const timeout = setTimeout(() => {
  console.error("Desktop preferences fixture timed out");
  app.exit(1);
}, 60000);
const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const xid = (window) => {
  const handle = window.getNativeWindowHandle();
  return (
    "0x" +
    (handle.length === 8 ? handle.readBigUInt64LE() : BigInt(handle.readUInt32LE())).toString(16)
  );
};
app
  .whenReady()
  .then(async () => {
    const controller = createController({ app }, () => ({}), { argv: [] });
    app.on("browser-window-created", (_event, created) => controller.observeWindow(created));
    const window = controller.createMainWindow(BrowserWindow, {
      show: false,
      width: 1000,
      height: 700,
      minWidth: 940,
      minHeight: 500,
      frame: false,
      webPreferences: { sandbox: true, contextIsolation: true },
    });
    await window.loadURL(
      `data:text/html,${encodeURIComponent(fs.readFileSync(path.join(__dirname, "titlebars.html"), "utf8"))}`,
    );
    const styles = (ids) =>
      window.webContents.executeJavaScript(
        `Object.fromEntries(${JSON.stringify(ids)}.map(id=>[id,getComputedStyle(document.getElementById(id)).display]))`,
      );
    const actions = ["minimize", "maximize", "close"];
    const legacyActions = actions.map((action) => `legacy-${action}`);
    const geometry = () =>
      window.webContents.executeJavaScript(`(() => {
      const rect = id => document.getElementById(id).getBoundingClientRect().toJSON();
      const bar = rect('bar'), help = rect('help'), controls = rect('controls');
      return { bar, help, controls, trailingInset: getComputedStyle(document.getElementById('bar')).paddingInlineEnd,
        buttons: ['minimize','maximize','close'].map(rect), toolbar: rect('toolbar'), activity: rect('activity'), wordmark: rect('wordmark') };
    })()`);
    const originalGeometry = await geometry();
    for (const action of actions) {
      await controller.apply({ [action + "Button"]: false });
      const current = await styles(actions);
      const legacy = await styles(legacyActions);
      for (const item of actions) {
        assert.equal(current[item], item === action ? "none" : "flex");
        assert.equal(legacy[`legacy-${item}`], item === action ? "none" : "flex");
      }
      const next = await geometry();
      assert.equal(next.buttons[actions.indexOf(action)].width, 0);
      assert.ok(next.controls.width < originalGeometry.controls.width);
      assert.equal(next.trailingInset, "0px", "remaining window buttons still meet the edge");
      assert.deepEqual(next.toolbar, originalGeometry.toolbar);
      assert.deepEqual(next.activity, originalGeometry.activity);
      assert.deepEqual(next.wordmark, originalGeometry.wordmark);
    }
    window.showInactive();
    const capture = async (name) => {
      await window.webContents.executeJavaScript(
        "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
      );
      fs.writeFileSync(
        path.join(output, name + ".png"),
        (await window.webContents.capturePage()).toPNG(),
      );
    };
    await controller.apply({});
    await capture("controls-enabled");
    for (const settings of [
      { windowControls: false },
      { minimizeButton: false, maximizeButton: false, closeButton: false },
      { nativeTitlebar: true },
    ]) {
      await controller.apply(settings);
      assert.equal((await geometry()).controls.width, 0);
      assert.equal((await geometry()).bar.right - (await geometry()).help.right, 12);
      assert.equal((await geometry()).trailingInset, "12px");
      assert.deepEqual(
        await styles(legacyActions),
        Object.fromEntries(legacyActions.map((id) => [id, "none"])),
      );
      assert.deepEqual(await styles(["toolbar", "activity", "wordmark"]), {
        toolbar: "block",
        activity: "flex",
        wordmark: "block",
      });
    }
    await capture("controls-disabled");
    await window.webContents.executeJavaScript(
      'document.documentElement.style.setProperty("--space-12","20px")',
    );
    assert.equal((await geometry()).bar.right - (await geometry()).help.right, 20);
    await window.webContents.executeJavaScript('document.documentElement.dir="rtl"');
    assert.equal((await geometry()).help.left - (await geometry()).bar.left, 20);
    await capture("controls-disabled-rtl");
    await window.webContents.executeJavaScript(
      'document.documentElement.dir="ltr"; document.documentElement.style.removeProperty("--space-12")',
    );
    await controller.apply({});
    assert.deepEqual(
      await geometry(),
      originalGeometry,
      "restoring controls restores upstream geometry",
    );
    const thirdParty = new BrowserWindow({ show: false, width: 500, height: 400 });
    await thirdParty.loadURL(
      `data:text/html,${encodeURIComponent('<div class="winButtons_thirdParty"><button class="winButton_thirdParty" id="min">Minimize</button><button class="winButton_thirdParty" id="max">Maximize</button><button class="winButton_thirdParty" id="close">Close</button></div><button id="other">Unrelated action</button>')}`,
    );
    await controller.apply({ minimizeButton: false, closeButton: false });
    assert.deepEqual(
      await thirdParty.webContents.executeJavaScript(
        `['min','max','close','other'].map(id=>getComputedStyle(document.getElementById(id)).display)`,
      ),
      ["none", "inline-block", "none", "inline-block"],
    );
    const nativeControlsSupported = typeof thirdParty.getWindowControls === "function";
    let nativeVisualControls;
    if (process.env.DPE_DESKTOP_REQUIRE_NATIVE_CONTROLS === "1")
      assert.equal(nativeControlsSupported, true);
    if (nativeControlsSupported) {
      assert.deepEqual(thirdParty.getWindowControls(), {
        minimize: false,
        maximize: true,
        close: false,
      });
      await controller.apply({ windowControls: false });
      assert.deepEqual(thirdParty.getWindowControls(), {
        minimize: false,
        maximize: false,
        close: false,
      });
      await controller.apply({ nativeTitlebar: true });
      assert.deepEqual(thirdParty.getWindowControls(), {
        minimize: true,
        maximize: true,
        close: true,
      });
      if (process.env.DPE_DESKTOP_X11_TOOLS) {
        const tools = process.env.DPE_DESKTOP_X11_TOOLS;
        const nativeTool = (name, args) =>
          execFileSync(path.join(tools, "usr/bin", name), args, {
            encoding: "utf8",
            env: { ...process.env, LD_LIBRARY_PATH: path.join(tools, "usr/lib") },
          });
        const capture = async (target, name) => {
          await delay(300);
          const tree = nativeTool("xwininfo", ["-id", xid(target), "-tree"]);
          const parent = /Parent window id: (0x[\da-f]+)/i.exec(tree)?.[1];
          const root = /Root window id: (0x[\da-f]+)/i.exec(tree)?.[1];
          const frame = parent && parent !== root ? parent : xid(target);
          const filename = path.join(output, name + ".png");
          execFileSync("import", ["-window", frame, filename]);
          return {
            filename,
            window: xid(target),
            frame,
            bounds: target.getContentBounds(),
            maximized: target.isMaximized(),
            minimized: target.isMinimized(),
            hints: execFileSync(
              "xprop",
              ["-id", xid(target), "_MOTIF_WM_HINTS", "_NET_WM_STATE", "_NET_FRAME_EXTENTS"],
              { encoding: "utf8" },
            ),
          };
        };
        thirdParty.setTitle("Third-party native controls");
        thirdParty.show();
        const captures = [];
        captures.push(await capture(thirdParty, "native-default"));
        for (const action of ["minimize", "maximize", "close"]) {
          await controller.apply({ [action + "Button"]: false });
          captures.push(await capture(thirdParty, "native-hide-" + action));
          assert.deepEqual(thirdParty.getContentBounds(), captures[0].bounds);
        }
        await controller.apply({ windowControls: false });
        captures.push(await capture(thirdParty, "native-hide-all"));
        const createdAfter = new BrowserWindow({
          title: "Created with controls disabled",
          width: 420,
          height: 320,
        });
        await createdAfter.loadURL("data:text/html,Created%20after%20disabling%20controls");
        assert.deepEqual(createdAfter.getWindowControls(), {
          minimize: false,
          maximize: false,
          close: false,
        });
        captures.push(await capture(createdAfter, "native-created-disabled"));
        createdAfter.destroy();
        const beforeRestore = thirdParty.getContentBounds();
        await controller.apply({ nativeTitlebar: true });
        captures.push(await capture(thirdParty, "native-restored-before-shortcuts"));
        assert.deepEqual(
          thirdParty.getContentBounds(),
          beforeRestore,
          "restoring native controls preserves content geometry",
        );
        await controller.apply({ windowControls: false });
        await delay(300);
        let closeAttempted = false;
        const preventClose = (event) => {
          event.preventDefault();
          closeAttempted = true;
        };
        thirdParty.on("close", preventClose);
        nativeTool("xdotool", ["windowactivate", "--sync", xid(thirdParty)]);
        nativeTool("xdotool", ["key", "--clearmodifiers", "alt+F4"]);
        await delay(300);
        assert.equal(
          closeAttempted,
          true,
          "Alt+F4 remains available when the native Close button is hidden",
        );
        thirdParty.removeListener("close", preventClose);
        nativeTool("xdotool", ["key", "--clearmodifiers", "alt+F10"]);
        await delay(300);
        assert.equal(
          thirdParty.isMaximized(),
          true,
          "WM maximize remains available with hidden native controls",
        );
        captures.push(await capture(thirdParty, "native-wm-maximized"));
        await controller.apply({ closeButton: false });
        captures.push(await capture(thirdParty, "native-wm-maximized-close-hidden"));
        assert.equal(thirdParty.isMaximized(), true);
        await controller.apply({ nativeTitlebar: true });
        captures.push(await capture(thirdParty, "native-wm-maximized-restored"));
        assert.equal(thirdParty.isMaximized(), true);
        await controller.apply({ windowControls: false });
        assert.equal(thirdParty.isMaximized(), true);
        nativeTool("xdotool", ["key", "--clearmodifiers", "alt+F10"]);
        await delay(300);
        assert.equal(thirdParty.isMaximized(), false);
        captures.push(await capture(thirdParty, "native-wm-unmaximized"));
        assert.deepEqual(
          thirdParty.getContentBounds(),
          beforeRestore,
          "changing controls while maximized preserves normal restore bounds",
        );
        nativeTool("xdotool", ["key", "--clearmodifiers", "alt+F9"]);
        await delay(300);
        assert.equal(
          thirdParty.isMinimized(),
          true,
          "WM minimize remains available with hidden native controls",
        );
        const saveDuringRestore = process.env.DPE_DESKTOP_RESTORE_SAVE === "1";
        if (!saveDuringRestore) await controller.apply({ nativeTitlebar: true });
        thirdParty.restore();
        thirdParty.show();
        if (saveDuringRestore) await controller.apply({ nativeTitlebar: true });
        captures.push(await capture(thirdParty, "native-wm-restored"));
        fs.writeFileSync(
          path.join(output, "native-transitions.json"),
          JSON.stringify(captures, null, 2),
        );
        assert.deepEqual(
          thirdParty.getContentBounds(),
          beforeRestore,
          "saving controls while minimized or restoring preserves normal content bounds",
        );
        await controller.apply({ nativeTitlebar: true });
        captures.push(await capture(thirdParty, "native-restored"));
        assert.deepEqual(thirdParty.getWindowControls(), {
          minimize: true,
          maximize: true,
          close: true,
        });
        nativeVisualControls = {
          captures,
          windowManagerShortcuts: ["Alt+F4", "Alt+F10", "Alt+F9"],
        };
      }
    }
    await controller.apply({ minimumWindowSize: false });
    assert.deepEqual(window.getMinimumSize(), [1, 1]);
    controller.setMinimumSize(window, 1050, 600);
    assert.deepEqual(window.getMinimumSize(), [1, 1]);
    await controller.apply({ minimumWindowSize: true });
    assert.deepEqual(window.getMinimumSize(), [1050, 600]);
    await controller.apply({ nativeTitlebar: true });
    assert.equal(controller.status().restartRequired, true);
    const native = controller.createAuxiliaryWindow(BrowserWindow, {
      show: false,
      width: 940,
      height: 500,
      minWidth: 940,
      minHeight: 500,
      frame: false,
    });
    await native.loadURL("data:text/html,Native%20titlebar%20probe");
    const decorations = (target) => {
      const hints = execFileSync("xprop", ["-id", xid(target), "_MOTIF_WM_HINTS"], {
        encoding: "utf8",
      });
      return {
        hints: hints.trim(),
        decorations: Number(hints.split("=")[1]?.split(",")[2]?.trim()),
      };
    };
    const framelessHints = decorations(window);
    const nativeHints = decorations(native);
    assert.equal(framelessHints.decorations, 0);
    assert.equal(nativeHints.decorations, 1);
    let minimizedWithoutTray;
    if (process.env.DPE_DESKTOP_EXPECT_WM === "1") {
      await controller.apply({ tray: false, startMinimized: true });
      const deadline = Date.now() + 3000;
      while (!window.isMinimized() && Date.now() < deadline)
        await new Promise((resolve) => setTimeout(resolve, 50));
      assert.equal(controller.startMinimized(), true);
      assert.equal(controller.minimizeToTray(true), false);
      assert.equal(window.isMinimized(), true);
      assert.equal(
        native.isVisible(),
        false,
        "auxiliary windows do not inherit hidden main-window restoration",
      );
      minimizedWithoutTray = true;
    }
    const result = {
      passed: true,
      individualButtons: true,
      legacyIndividualButtons: true,
      unrelatedControlsPreserved: true,
      disabledControlsCollapse: true,
      toolbarInsetRestored: true,
      rtlAndLiveSpacingTokens: true,
      thirdPartyCustomControls: true,
      thirdPartyNativeControls: nativeControlsSupported,
      nativeVisualControls,
      minimumSizeIPC: true,
      nativeFrameCreated: true,
      framelessHints,
      nativeHints,
      minimizedWithoutTray,
      versions: process.versions,
    };
    fs.writeFileSync(path.join(output, "result.json"), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result));
    window.destroy();
    thirdParty.destroy();
    native.destroy();
    clearTimeout(timeout);
    app.quit();
  })
  .catch((error) => {
    console.error(error);
    app.exit(1);
  });
