// Synthetic local document under an intercepted Discord origin: no network or account.
const { app, BrowserWindow, screen, session } = require("electron");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { windowOptions, corner } = {
  ...require("../../integration/desktop/popout.cjs"),
  ...require("../../integration/desktop/popout-position.cjs"),
};
const output = process.env.DPE_POPOUT_OUTPUT;
fs.mkdirSync(output, { recursive: true });
app.setPath("userData", path.join(output, "profile"));
app.on("window-all-closed", () => {});
const report = { passed: false, versions: process.versions, cases: [] };
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const timer = setTimeout(() => app.exit(1), 55000);
app
  .whenReady()
  .then(async () => {
    await session.defaultSession.protocol.handle("https", (request) =>
      request.url.endsWith("portrait.webm")
        ? new Response(fs.readFileSync(path.join(output, "../portrait.webm")), {
            headers: { "content-type": "video/webm" },
          })
        : new Response(
            `
    <style>html,body{margin:0;width:100%;height:100%;overflow:hidden}video{width:100%;height:100%}</style>
    <canvas hidden id="source" width="360" height="640"></canvas><video autoplay muted></video>
    <script>window.begin=()=>{const c=source.getContext('2d');
      setInterval(()=>{c.fillStyle='blue';c.fillRect(0,0,360,640)},40);
      document.querySelector('video').srcObject=source.captureStream(25);};</script>
  `,
            { headers: { "content-type": "text/html" } },
          ),
    );
    let panel;
    if (process.env.DPE_POPOUT_X11_WM === "1") {
      const area = screen.getPrimaryDisplay().bounds;
      panel = new BrowserWindow({
        frame: false,
        focusable: true,
        type: "dock",
        x: area.x,
        y: area.y + area.height - 48,
        width: area.width,
        height: 48,
      });
      await panel.loadURL("data:text/html,<body style='background:black'>Synthetic panel</body>");
      panel.showInactive();
      const xid = String(panel.getNativeWindowHandle().readUInt32LE());
      execFileSync("xprop", [
        "-id",
        xid,
        "-f",
        "_NET_WM_STRUT_PARTIAL",
        "32c",
        "-set",
        "_NET_WM_STRUT_PARTIAL",
        `0, 0, 0, 48, 0, 0, 0, 0, 0, 0, 0, ${area.width - 1}`,
      ]);
      execFileSync("xprop", [
        "-id",
        xid,
        "-f",
        "_NET_WM_STRUT",
        "32c",
        "-set",
        "_NET_WM_STRUT",
        "0, 0, 0, 48",
      ]);
      await delay(300);
      assert.ok(
        screen.getPrimaryDisplay().workArea.height <= area.height - 48,
        execFileSync("xprop", ["-root", "_NET_WORKAREA"], { encoding: "utf8" }),
      );
    }
    const preferences = JSON.parse(process.env.DPE_POPOUT_PREFS || "{}");
    const controller = require("../../integration/desktop/preferences.cjs").install(
      require("electron"),
      { getConfig: () => preferences },
    );
    const parent = new BrowserWindow({ title: "Placement parent", width: 900, height: 650 });
    await parent.loadURL("data:text/html,parent");
    if (process.env.DPE_POPOUT_SECOND_OUTPUT) {
      const name = process.env.DPE_POPOUT_SECOND_OUTPUT;
      assert.match(name, /^[A-Za-z0-9_.-]+$/);
      function findParent(node) {
        if (node.pid === process.pid && node.name === parent.getTitle()) return node.id;
        for (const c of [...(node.nodes || []), ...(node.floating_nodes || [])]) {
          const id = findParent(c);
          if (id) return id;
        }
      }
      let id;
      for (let attempt = 0; attempt < 40; attempt++) {
        const tree = JSON.parse(
          execFileSync("swaymsg", ["-r", "-t", "get_tree"], { encoding: "utf8" }),
        );
        id = findParent(tree);
        if (id) break;
        await delay(25);
      }
      assert.ok(id);
      execFileSync("swaymsg", [`[con_id=${id}] move container to output ${name}`]);
      execFileSync("swaymsg", [`[con_id=${id}] focus`]);
      await delay(300);
    }
    for (const mode of process.env.DPE_POPOUT_BACKEND === "gnome"
      ? ["delayed-media"]
      : ["saved-geometry", "delayed-media", "user-resize", "user-move", "focus-lost"]) {
      const defaults = {
        frame: false,
        resizable: false,
        minWidth: 320,
        minHeight: 180,
        ...(mode === "saved-geometry" ? { x: 70, y: 70, width: 640, height: 360 } : {}),
      };
      const options = windowOptions(
        defaults,
        "DISCORD_CHANNEL_CALL_POPOUT",
        parent,
        screen,
        defaults,
        preferences,
      );
      const child = new BrowserWindow({ ...options, title: "DPE placement child" });
      child.windowKey = "DISCORD_CHANNEL_CALL_POPOUT";
      controller.attachPopout(child, options);
      await child.loadURL("https://canary.discord.com/popout");
      child.focus();
      // Xvfb has no WM to honor _NET_ACTIVE_WINDOW; focus our owned XID.
      if (process.env.DPE_POPOUT_BACKEND === "x11") {
        const handle = child.getNativeWindowHandle();
        execFileSync("xdotool", ["windowfocus", String(handle.readUInt32LE())]);
      }
      await delay(700);
      function geometry() {
        const display = screen.getDisplayMatching(parent.getBounds());
        let bounds = child.getBounds();
        if (process.env.NIRI_SOCKET) {
          const windows = JSON.parse(
            execFileSync("niri", ["msg", "-j", "windows"], { encoding: "utf8" }),
          );
          const item = windows.find((w) => w.pid === process.pid && w.title === child.getTitle());
          assert.equal(item?.is_floating, true);
          const [x, y] = item.layout.tile_pos_in_workspace_view;
          const [width, height] = item.layout.tile_size;
          bounds = { x, y, width, height };
          // Fixture config: gaps 24; left/right/top/bottom struts 20/50/30/40.
          // The PiP inset is max(16, gaps + strut), inside the real panel area.
          return {
            bounds,
            display: {
              ...display,
              workArea: {
                x: display.bounds.x + 28,
                y: display.bounds.y + 38 + Number(process.env.DPE_POPOUT_PANEL || 0),
                width: display.bounds.width - 86,
                height: display.bounds.height - 86 - Number(process.env.DPE_POPOUT_PANEL || 0),
              },
            },
          };
        } else if (process.env.HYPRLAND_INSTANCE_SIGNATURE) {
          const clients = JSON.parse(
            execFileSync("hyprctl", ["-j", "clients"], { encoding: "utf8" }),
          );
          const item = clients.find((w) => w.pid === process.pid && w.title === child.getTitle());
          assert.ok(item?.floating);
          const outputs = JSON.parse(
            execFileSync("hyprctl", ["-j", "monitors"], { encoding: "utf8" }),
          );
          const monitor = outputs.find((o) => o.id === item.monitor);
          const option = (name) =>
            JSON.parse(
              execFileSync("hyprctl", ["-j", "getoption", `general.${name}`], { encoding: "utf8" }),
            );
          const gaps = (option("float_gaps").css || "0 0 0 0").split(" ").map(Number);
          const [gt, gr, gb, gl] = gaps;
          const [left, top, right, bottom] = monitor.reserved.map(
            (n, i) => n + [gl, gt, gr, gb][i],
          );
          const border =
            JSON.parse(
              execFileSync("hyprctl", ["-j", "getprop", `address:${item.address}`, "border_size"], {
                encoding: "utf8",
              }),
            ).border_size ?? option("border_size").int;
          return {
            bounds: {
              x: item.at[0] - border,
              y: item.at[1] - border,
              width: item.size[0] + 2 * border,
              height: item.size[1] + 2 * border,
            },
            display: {
              ...display,
              workArea: {
                x: monitor.x + left,
                y: monitor.y + top,
                width: monitor.width / monitor.scale - left - right,
                height: monitor.height / monitor.scale - top - bottom,
              },
            },
          };
        } else if (process.env.DPE_POPOUT_BACKEND === "kwin") {
          const source = require("../../integration/desktop/kwin-popout.cjs")
            .toString()
            .replace(
              "workspace.activeWindow",
              "workspace.windowList().find(w=>w.pid===request.pid&&w.caption===request.title)",
            );
          const data = JSON.parse(
            execFileSync(
              "/usr/bin/python3",
              [
                path.resolve(__dirname, "../../integration/desktop/kwin-popout.py"),
                `()=>(${source})(${JSON.stringify({ pid: process.pid, title: child.getTitle() })})`,
              ],
              { encoding: "utf8" },
            ),
          );
          assert.ok(data);
          return {
            bounds: data.bounds,
            display: { ...display, workArea: data.area },
            alwaysOnTop: data.keepAbove,
          };
        } else if (process.env.SWAYSOCK) {
          const tree = JSON.parse(
            execFileSync("swaymsg", ["-r", "-t", "get_tree"], { encoding: "utf8" }),
          );
          function find(node, area, floating = false) {
            if (node.type === "workspace") area = node.rect;
            if (node.pid === process.pid && node.name === child.getTitle()) {
              assert.equal(floating, true);
              return { bounds: node.rect, display: { ...display, workArea: area } };
            }
            for (const c of node.nodes || []) {
              const result = find(c, area, floating);
              if (result) return result;
            }
            for (const c of node.floating_nodes || []) {
              const result = find(c, area, true);
              if (result) return result;
            }
          }
          const result = find(tree);
          assert.ok(result, "Sway owns the child");
          return result;
        }
        return { bounds, display };
      }
      function checkCorner(stage) {
        const { bounds, display } = geometry();
        if (
          process.env.DPE_POPOUT_BACKEND === "gnome" ||
          preferences.popoutAutoPosition === false
        ) {
          assert.ok(
            bounds.width <= display.workArea.width && bounds.height <= display.workArea.height,
          );
          report.cases.push({ mode, stage, bounds, compositorControlsPosition: true });
          return;
        }
        const expected = corner(
          display.workArea,
          bounds.width,
          bounds.height,
          preferences.popoutCorner,
        );
        assert.ok(
          Math.abs(bounds.x - expected.x) <= 2 && Math.abs(bounds.y - expected.y) <= 2,
          JSON.stringify({ stage, bounds, expected, display }),
        );
        report.cases.push({ mode, stage, bounds, expected });
      }
      checkCorner("before-metadata");
      assert.equal(child.isResizable(), true, "Renderer resizable=false must not lock PiP");
      assert.deepEqual(child.getMaximumSize(), [0, 0], "Startup maximum constraints are lifted");
      if (mode === "saved-geometry")
        assert.equal(
          await child.webContents.executeJavaScript(
            "window.DPEPiP.state().then(state=>state.available)",
          ),
          true,
        );

      if (mode === "user-resize") {
        if (process.env.HYPRLAND_INSTANCE_SIGNATURE) {
          const clients = JSON.parse(
            execFileSync("hyprctl", ["-j", "clients"], { encoding: "utf8" }),
          );
          const item = clients.find((w) => w.pid === process.pid && w.title === child.getTitle());
          execFileSync("hyprctl", [
            "dispatch",
            `hl.dsp.window.resize({window="address:${item.address}",x=600,y=400,relative=false})`,
          ]);
        } else child.setSize(600, 400);
        await delay(150);
      }
      if (mode === "user-move") {
        if (process.env.NIRI_SOCKET) {
          const windows = JSON.parse(
            execFileSync("niri", ["msg", "-j", "windows"], { encoding: "utf8" }),
          );
          const item = windows.find((w) => w.pid === process.pid && w.title === child.getTitle());
          execFileSync("niri", [
            "msg",
            "action",
            "move-floating-window",
            "--id",
            String(item.id),
            "--x",
            "-120",
            "--y",
            "-80",
          ]);
        } else if (process.env.SWAYSOCK) {
          const tree = JSON.parse(
            execFileSync("swaymsg", ["-r", "-t", "get_tree"], { encoding: "utf8" }),
          );
          function find(node) {
            if (node.pid === process.pid && node.name === child.getTitle()) return node.id;
            for (const c of [...(node.nodes || []), ...(node.floating_nodes || [])]) {
              const id = find(c);
              if (id) return id;
            }
          }
          execFileSync("swaymsg", [`[con_id=${find(tree)}] move absolute position 80 px 80 px`]);
        } else if (process.env.HYPRLAND_INSTANCE_SIGNATURE) {
          const clients = JSON.parse(
            execFileSync("hyprctl", ["-j", "clients"], { encoding: "utf8" }),
          );
          const item = clients.find((w) => w.pid === process.pid && w.title === child.getTitle());
          execFileSync("hyprctl", [
            "dispatch",
            `hl.dsp.window.move({window="address:${item.address}",x=80,y=80,relative=false})`,
          ]);
        } else if (process.env.DPE_POPOUT_BACKEND === "kwin") {
          const source = require("../../integration/desktop/kwin-popout.cjs").toString();
          const data = geometry();
          execFileSync("/usr/bin/python3", [
            path.resolve(__dirname, "../../integration/desktop/kwin-popout.py"),
            `()=>(${source})(${JSON.stringify({ pid: process.pid, before: data.bounds, target: { x: 80, y: 80 } })})`,
          ]);
        } else child.setPosition(80, 80);
        await delay(200);
      }
      if (mode === "focus-lost") {
        if (process.env.NIRI_SOCKET) {
          const windows = JSON.parse(
            execFileSync("niri", ["msg", "-j", "windows"], { encoding: "utf8" }),
          );
          const item = windows.find((w) => w.pid === process.pid && w.title === parent.getTitle());
          execFileSync("niri", ["msg", "action", "focus-window", "--id", String(item.id)]);
        } else if (process.env.SWAYSOCK) {
          execFileSync("swaymsg", ['[title="^Placement parent$"] focus']);
        } else if (process.env.HYPRLAND_INSTANCE_SIGNATURE) {
          const clients = JSON.parse(
            execFileSync("hyprctl", ["-j", "clients"], { encoding: "utf8" }),
          );
          const item = clients.find((w) => w.pid === process.pid && w.title === parent.getTitle());
          execFileSync("hyprctl", ["dispatch", `hl.dsp.focus({window="address:${item.address}"})`]);
        } else if (process.env.DPE_POPOUT_BACKEND === "kwin") {
          execFileSync("/usr/bin/python3", [
            path.resolve(__dirname, "../../integration/desktop/kwin-popout.py"),
            `()=>{const w=workspace.windowList().find(w=>w.pid===${process.pid}&&w.caption===${JSON.stringify(parent.getTitle())});if(w)workspace.activeWindow=w;return null;}`,
          ]);
        } else parent.focus();
        await delay(150);
        assert.equal(child.isFocused(), false);
      }
      if (mode === "user-resize") {
        const before = geometry().bounds;
        await child.webContents.executeJavaScript("window.DPEPiP.state()");
        child.setFullScreen(true);
        await delay(300);
        assert.equal(child.isFullScreen(), true);
        child.setFullScreen(false);
        await delay(1000);
        assert.equal(child.isFullScreen(), false);
        assert.equal(child.isResizable(), true);
        assert.deepEqual(child.getMaximumSize(), [0, 0]);
        const after = geometry().bounds;
        assert.ok(
          ["x", "y", "width", "height"].every((key) => Math.abs(before[key] - after[key]) <= 2),
          JSON.stringify({ before, after }),
        );
      }
      const preserved = geometry().bounds;
      if (
        ["user-move", "user-resize"].includes(mode) &&
        process.env.DPE_POPOUT_BACKEND !== "gnome"
      ) {
        const pip = require("../../integration/desktop/popout.cjs").session(child);
        assert.equal((await pip.state()).corner, null);
        await pip.apply(
          { ...preferences, popoutAutoPosition: true, popoutCorner: "top-right" },
          { popoutCorner: preferences.popoutCorner },
        );
        assert.deepEqual(geometry().bounds, preserved, "Settings preserve a manually changed PiP");
      }
      await child.webContents.executeJavaScript(
        process.env.DPE_POPOUT_BACKEND === "gnome"
          ? 'document.querySelector("video").src="https://canary.discord.com/portrait.webm"'
          : "begin()",
      );
      await delay(1200);
      if (mode === "saved-geometry") {
        const size = child.getSize();
        assert.ok(size[0] < size[1], "Reopened PiP uses the current portrait stream");
        assert.ok(Math.abs(size[0] - (size[1] * 360) / 640) <= 1, JSON.stringify(size));
        checkCorner("reopened-metadata");
      } else if (mode !== "delayed-media") {
        assert.deepEqual(geometry().bounds, preserved, `Preserve ${mode}`);
        report.cases.push({ mode, stage: "preserved", bounds: preserved });
      } else {
        assert.ok(
          child.getSize()[0] < child.getSize()[1],
          JSON.stringify({
            size: child.getSize(),
            focused: child.isFocused(),
            visible: child.isVisible(),
            media: await child.webContents.executeJavaScript(
              '({visible:document.visibilityState,width:document.querySelector("video").videoWidth,height:document.querySelector("video").videoHeight,ready:document.querySelector("video").readyState})',
            ),
          }),
        );
        checkCorner("portrait-metadata");
        const measuredSize = child.getSize();
        assert.ok(
          Math.abs(measuredSize[0] - (measuredSize[1] * 360) / 640) <= 1,
          JSON.stringify(measuredSize),
        );
        controller.setMinimumSize(child, 1280, 720);
        await delay(80);
        assert.deepEqual(
          child.getSize(),
          measuredSize,
          "Renderer minimum changes cannot stretch a PiP away from its preset",
        );
        const actual = geometry().bounds;
        assert.ok(actual.width < actual.height, JSON.stringify(actual));
        if (
          process.env.DPE_POPOUT_BACKEND !== "gnome" &&
          preferences.popoutAutoPosition !== false
        ) {
          const normal = geometry().bounds;
          const selected = await child.webContents.executeJavaScript(
            "window.DPEPiP.state().then(value=>value.corner)",
          );
          child.setFullScreen(true);
          await delay(300);
          child.setFullScreen(false);
          await delay(1000);
          assert.equal(
            await child.webContents.executeJavaScript(
              "window.DPEPiP.state().then(value=>value.corner)",
            ),
            selected,
            JSON.stringify({ normal, restored: geometry().bounds, size: child.getBounds() }),
          );
          const restored = geometry().bounds;
          assert.ok(
            ["x", "y", "width", "height"].every(
              (key) => Math.abs(normal[key] - restored[key]) <= 2,
            ),
            JSON.stringify({ normal, restored }),
          );
          assert.equal(child.isResizable(), true);
          await child.webContents.executeJavaScript(
            "document.documentElement.requestFullscreen()",
            true,
          );
          await delay(300);
          assert.equal(
            await child.webContents.executeJavaScript("!!document.fullscreenElement"),
            true,
          );
          await child.webContents.executeJavaScript("document.exitFullscreen()");
          await delay(1000);
          assert.equal(child.isResizable(), true);
          assert.equal(
            await child.webContents.executeJavaScript(
              "window.DPEPiP.state().then(value=>value.corner)",
            ),
            selected,
            "HTML fullscreen preserves the highlight",
          );
          const pip = require("../../integration/desktop/popout.cjs").session(child);
          for (const next of ["top-left", "bottom-right"]) {
            const previous = preferences.popoutCorner || "bottom-right";
            await pip.apply(
              { ...preferences, popoutAutoPosition: true, popoutCorner: next },
              { popoutCorner: previous },
            );
            preferences.popoutCorner = next;
            checkCorner("live-setting");
          }
          assert.equal(
            await child.webContents.executeJavaScript("typeof window.DPEPiP?.choose"),
            "function",
          );
          await child.webContents.executeJavaScript('window.DPEPiP.choose("top-right")');
          preferences.popoutCorner = "top-right";
          checkCorner("renderer-picker");
          assert.equal(
            await child.webContents.executeJavaScript(
              'document.querySelectorAll(".dpe-pip-resize").length',
            ),
            0,
          );
          assert.equal(
            await child.webContents.executeJavaScript("typeof window.DPEPiP.resize"),
            "undefined",
          );
          child.setSize(400, 280);
          await delay(200);
          assert.equal(child.isResizable(), true);
          assert.deepEqual(child.getMaximumSize(), [0, 0]);
          if (["x11", "kwin"].includes(process.env.DPE_POPOUT_BACKEND)) {
            const onTop = () =>
              process.env.DPE_POPOUT_BACKEND === "kwin"
                ? geometry().alwaysOnTop
                : child.isAlwaysOnTop();
            assert.equal(onTop(), true, "PiP starts topmost");
            await controller.apply({ ...preferences, popoutAlwaysOnTop: false });
            assert.equal(onTop(), false, "Topmost can be disabled live");
            await controller.apply({ ...preferences, popoutAlwaysOnTop: true });
            assert.equal(onTop(), true, "Topmost can be enabled live");
          }
        }
      }
      child.destroy();
    }
    parent.destroy();
    panel?.destroy();
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
