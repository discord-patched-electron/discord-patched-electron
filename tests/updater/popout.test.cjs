const assert = require("node:assert/strict");
const { test } = require("node:test");
const { windowOptions, mediaSize } = require("../../integration/desktop/popout.cjs");
const parent = {
  isDestroyed: () => false,
  getBounds: () => ({ x: 50, y: 50, width: 900, height: 700 }),
};
const display = { scaleFactor: 2, workArea: { x: 0, y: 30, width: 1280, height: 690 } };
const second = { workArea: { x: -1920, y: 0, width: 1920, height: 1080 } };
const screen = { getAllDisplays: () => [display, second], getDisplayMatching: () => display };
const options = {
  width: 854,
  height: 480,
  minWidth: 320,
  minHeight: 180,
  frame: false,
  resizable: true,
  webPreferences: { sandbox: false, nodeIntegration: false, contextIsolation: true },
};
const key = "DISCORD_CALL_TILE_POPOUT_123_stream:456";

test("video popouts are resizable transient dialogs in logical pixels with working renderers", () => {
  for (const name of [key, "DISCORD_CHANNEL_CALL_POPOUT"]) {
    const result = windowOptions(options, name, parent, screen);
    assert.equal(result.parent, parent);
    assert.equal(result.type, "dialog");
    assert.equal(result.modal, false);
    assert.equal(result.resizable, false);
    assert.equal(result.dpeResizable, true);
    assert.equal(result.minWidth, result.maxWidth);
    assert.equal(result.minHeight, result.maxHeight);
    assert.equal(result.width, 512);
    assert.equal(result.height, 288);
    assert.deepEqual(result.webPreferences, {
      ...options.webPreferences,
      backgroundThrottling: false,
    });
    assert.equal(options.parent, undefined);
  }
});

test("unrelated popouts and overlays retain their original options", () => {
  for (const name of [
    undefined,
    "DISCORD_RTC_DEBUG_POPOUT",
    "DISCORD_ACTIVITY_POPOUT",
    "DISCORD_SOUNDBOARD",
    "DISCORD_OutOfProcessOverlay",
  ])
    assert.equal(windowOptions(options, name, parent, screen), options);
  const overlay = { ...options, outOfProcessOverlay: true };
  assert.equal(windowOptions(overlay, key, parent, screen), overlay);
  assert.equal(windowOptions(options, key, null, screen), options);
  assert.equal(windowOptions(options, key, { isDestroyed: () => true }, screen), options);
});

test("restore valid geometry on secondary displays and recover disconnected or oversized geometry", () => {
  const saved = windowOptions(
    { ...options, x: -1800, y: 100, width: 640, height: 360 },
    key,
    parent,
    screen,
    undefined,
    { popoutAutoPosition: false },
  );
  assert.deepEqual([saved.x, saved.y, saved.width, saved.height], [-1800, 100, 640, 360]);
  const stale = windowOptions(
    { ...options, x: 50, y: 50, width: 640, height: 600 },
    key,
    parent,
    screen,
  );
  assert.equal(stale.dpeAutoSize, true, "Reopening still measures the current stream");
  assert.deepEqual([stale.width, stale.height], [640, 360], "No stream starts at 16:9");
  const large = windowOptions(
    { ...options, x: 9000, y: 9000, width: 3840, height: 2160 },
    key,
    parent,
    screen,
  );
  assert.ok(large.width <= 1248 && large.height <= 658);
  assert.ok(Math.abs(large.width / large.height - 16 / 9) < 0.01);
  assert.ok(large.x >= 0 && large.x + large.width <= 1280);
  assert.ok(large.y >= 30 && large.y + large.height <= 720);
  const invalid = windowOptions(
    { ...options, width: NaN, height: -10, x: Infinity, y: 0 },
    key,
    parent,
    screen,
  );
  assert.equal(invalid.width, 512);
  assert.equal(invalid.height, 288);
});

test("small work areas and undersized requests cannot violate size constraints", () => {
  const tiny = {
    getAllDisplays: () => [],
    getDisplayMatching: () => ({ workArea: { x: 0, y: 0, width: 300, height: 160 } }),
  };
  const result = windowOptions(options, key, parent, tiny);
  assert.ok(result.width <= 300 && result.height <= 160);
  assert.ok(result.minWidth <= result.width && result.minHeight <= result.height);
  const small = windowOptions({ ...options, width: 20, height: 10 }, key, parent, screen);
  assert.equal(small.width, 512);
  assert.equal(small.height, 288);
});

test("floating opt-out removes transient and fixed-size hints without losing bounded sizing", () => {
  const result = windowOptions(options, key, parent, screen, options, { popoutFloating: false });
  assert.equal(result.parent, undefined);
  assert.equal(result.type, undefined);
  assert.equal(result.resizable, true);
  assert.equal(result.dpeMinimum, undefined);
  assert.equal(result.dpeFloating, false);
  assert.equal(result.minWidth, 320);
  assert.equal(result.width, 512);
  assert.deepEqual([result.x, result.y], [752, 416]);
});

test("media sizing preserves portrait and ultrawide aspect, scaling once and accounting for chrome", () => {
  for (const [width, height] of [
    [1920, 1080],
    [1080, 1920],
    [3440, 1440],
    [640, 480],
  ]) {
    const size = mediaSize(display, { width, height }, { width: 0, height: 28 });
    assert.ok(Math.abs(size.width / (size.height - 28) - width / height) < 0.02);
    assert.ok(size.width <= 512 && size.height <= 311);
  }
  assert.deepEqual(mediaSize(display, { width: 640, height: 360 }), { width: 320, height: 180 });
  assert.deepEqual(mediaSize({ ...display, scaleFactor: 1 }, { width: 640, height: 360 }), {
    width: 512,
    height: 288,
  });
});

test("late metadata sizes once; a user's intervening resize and saved geometry are respected", async () => {
  const { EventEmitter } = require("node:events");
  const { attach } = require("../../integration/desktop/popout.cjs");
  for (const mode of ["unchanged", "resize", "blur"]) {
    const cancel = mode !== "unchanged";
    let resolve, changed;
    let resized = false;
    const web = Object.assign(new EventEmitter(), {
      getURL: () => "https://canary.discord.com/popout",
      executeJavaScript: () =>
        new Promise((done) => {
          resolve = done;
        }),
      getZoomFactor: () => 1,
    });
    const window = Object.assign(new EventEmitter(), {
      windowKey: key,
      webContents: web,
      isDestroyed: () => false,
      isMaximized: () => false,
      isFullScreen: () => false,
      getBounds: () => ({ x: 50, y: 50, width: resized ? 600 : 512, height: 288 }),
      getContentBounds: () => ({ width: 512, height: 288 }),
      setMinimumSize: () => {},
      setBounds: (bounds) => {
        changed = bounds;
      },
    });
    attach(
      window,
      { dpeVideo: true, dpeAutoSize: true, minWidth: 320, minHeight: 180 },
      () => {},
      screen,
    );
    window.emit("focus");
    if (mode === "resize") {
      resized = true;
      window.emit("resize");
    } else if (mode === "blur") {
      window.emit("blur");
      window.emit("focus");
    } else window.emit("move");
    resolve({ width: 1080, height: 1920, chromeWidth: 0, chromeHeight: 0 });
    await new Promise((done) => setImmediate(done));
    if (cancel) assert.equal(changed, undefined);
    else {
      assert.ok(changed.width < changed.height);
      assert.equal(changed.y + changed.height, 704);
      assert.equal(changed.x + changed.width, 1264);
    }
  }
});

test("niri placement targets a verified child ID and leaves unrelated or retiled windows alone", async () => {
  const { identify } = require("../../integration/desktop/popout-position.cjs");
  for (const mode of ["match", "foreign", "retiled", "focus-moved"]) {
    const calls = [];
    let count = 0;
    const run = async (_command, args) => {
      calls.push(args);
      if (args[2] === "workspaces") return { stdout: JSON.stringify([{ id: 7, output: "DP-1" }]) };
      if (args[2] === "outputs")
        return {
          stdout: JSON.stringify({
            "DP-1": { logical: { x: 0, y: 0, width: 1280, height: 720, scale: 1 } },
          }),
        };
      return {
        stdout: JSON.stringify([
          {
            id: 19,
            pid: mode === "foreign" ? process.pid + 1 : process.pid,
            title: "Discord Popout",
            is_focused: true,
            is_floating: mode !== "retiled" || count++ === 0,
            workspace_id: 7,
            layout: {
              window_size: [512, 288],
              tile_size: [512, 288],
              tile_pos_in_workspace_view: [384, 216],
            },
          },
        ]),
      };
    };
    const window = {
      isDestroyed: () => false,
      isFocused: () => mode !== "focus-moved",
      getTitle: () => "Discord Popout",
    };
    await identify(window, run, { NIRI_SOCKET: "/fixture" }).place(display.workArea);
    const moves = calls.filter((args) => args.includes("move-floating-window"));
    assert.equal(moves.length, mode === "match" ? 1 : 0);
    if (moves.length)
      assert.deepEqual(moves[0], [
        "msg",
        "action",
        "move-floating-window",
        "--id",
        "19",
        "--x",
        "+368",
        "--y",
        "+200",
      ]);
  }
});

test("niri resolves the child's monitor for sizing despite missing Wayland window coordinates", async () => {
  const { identify } = require("../../integration/desktop/popout-position.cjs");
  const logical = { x: 1280, y: 0, width: 1920, height: 1080, scale: 1.5 };
  const data = {
    windows: [
      {
        id: 19,
        pid: process.pid,
        title: "PiP",
        is_focused: true,
        is_floating: true,
        workspace_id: 7,
      },
    ],
    workspaces: [{ id: 7, output: "DP-1" }],
    outputs: { "DP-1": { logical } },
  };
  const run = async (_command, args) => ({ stdout: JSON.stringify(data[args[2]]) });
  const window = { isDestroyed: () => false, isFocused: () => true, getTitle: () => "PiP" };
  const controller = identify(window, run, { NIRI_SOCKET: "/fixture" });
  assert.deepEqual(
    await controller.display({
      getDisplayMatching: (rect) => {
        assert.deepEqual(rect, logical);
        return second;
      },
    }),
    { ...second, scaleFactor: 1.5 },
  );
});

test("reopening a hidden or minimized video popout shows it without clearing its pin", () => {
  const { focusExisting } = require("../../integration/desktop/popout.cjs");
  for (const minimized of [true, false]) {
    const calls = [];
    const window = {
      isVisible: () => false,
      isMinimized: () => minimized,
      restore: () => calls.push("restore"),
      show: () => calls.push("show"),
      focus: () => calls.push("focus"),
      setAlwaysOnTop: () => assert.fail("Must preserve the pin state"),
    };
    focusExisting(window, key);
    assert.deepEqual(calls, minimized ? ["restore", "show", "focus"] : ["show", "focus"]);
  }
});

test("manual PiP tracing records lifecycle stages without channel or participant IDs", () => {
  const fs = require("node:fs");
  const os = require("node:os");
  const path = require("node:path");
  const { traceRequest } = require("../../integration/desktop/popout.cjs");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dpe-pip-trace-"));
  const previous = process.env.DPE_POPOUT_TRACE_FILE;
  try {
    process.env.DPE_POPOUT_TRACE_FILE = path.join(dir, "trace.jsonl");
    traceRequest({ frameName: key, url: "https://canary.discord.com/private" });
    traceRequest({ frameName: "DISCORD_RTC_DEBUG_POPOUT" });
    const rows = fs.readFileSync(process.env.DPE_POPOUT_TRACE_FILE, "utf8").trim().split("\n");
    assert.equal(rows.length, 1);
    const entry = JSON.parse(rows[0]);
    assert.equal(entry.stage, "requested");
    assert.equal(entry.kind, "tile");
    assert.deepEqual(Object.keys(entry).sort(), ["kind", "stage", "time"]);
    process.env.DPE_POPOUT_TRACE_FILE = path.join(dir, "missing", "trace.jsonl");
    assert.doesNotThrow(() => traceRequest({ frameName: key }));
  } finally {
    if (previous === undefined) delete process.env.DPE_POPOUT_TRACE_FILE;
    else process.env.DPE_POPOUT_TRACE_FILE = previous;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("niri uses decorated, committed geometry and output-local deltas on offset monitors", async () => {
  const { identify } = require("../../integration/desktop/popout-position.cjs");
  const calls = [];
  let reads = 0;
  const run = async (_command, args) => {
    calls.push(args);
    const kind = args[2];
    const child = {
      id: 9,
      pid: process.pid,
      title: "PiP",
      is_focused: true,
      is_floating: true,
      workspace_id: 2,
      layout: {
        window_size: ++reads < 3 ? [512, 288] : [300, 500],
        tile_size: reads < 3 ? [520, 296] : [308, 508],
        tile_pos_in_workspace_view: [200, 100],
      },
    };
    const data = {
      windows: [child],
      workspaces: [{ id: 2, output: "DP-2" }],
      outputs: { "DP-2": { logical: { x: -1280, y: 500, width: 1280, height: 720, scale: 1.25 } } },
    };
    return { stdout: JSON.stringify(data[kind] || {}) };
  };
  const window = { isDestroyed: () => false, isFocused: () => true, getTitle: () => "PiP" };
  await identify(window, run, { NIRI_SOCKET: "/fixture" }).place(
    { x: -1280, y: 530, width: 1280, height: 690 },
    { width: 300, height: 500 },
  );
  const moves = calls.filter((args) => args.includes("move-floating-window"));
  assert.equal(moves.length, 1);
  assert.deepEqual(moves[0].slice(-4), ["--x", "+756", "--y", "+96"]);
});

test("Sway places only the identified floating child inside its workspace including panel reservations", async () => {
  const { identify } = require("../../integration/desktop/popout-position.cjs");
  for (const mode of ["match", "foreign", "ambiguous", "retiled", "cancelled", "missing"]) {
    const calls = [];
    let reads = 0;
    const run = async (command, args) => {
      assert.equal(command, "swaymsg");
      calls.push(args);
      if (mode === "missing") throw Error("ENOENT");
      if (!args.includes("get_tree")) return { stdout: '[{"success":true}]' };
      const child = {
        id: 34,
        pid: mode === "foreign" ? process.pid + 1 : process.pid,
        focused: true,
        name: "PiP",
        rect: { x: -1000, y: 120, width: 520, height: 320 },
        window_rect: { width: 512, height: 288 },
      };
      const children = mode === "ambiguous" ? [child, { ...child, id: 35 }] : [child];
      const tiled = mode === "retiled" && reads++ > 0;
      return {
        stdout: JSON.stringify({
          nodes: [
            {
              type: "workspace",
              rect: { x: -1920, y: 30, width: 1920, height: 1050 },
              nodes: tiled ? children : [],
              floating_nodes: tiled ? [] : children,
            },
          ],
        }),
      };
    };
    const window = { isDestroyed: () => false, isFocused: () => true, getTitle: () => "PiP" };
    const controller = identify(window, run, { SWAYSOCK: "/fixture" });
    await controller.place(display.workArea, undefined, () => mode !== "cancelled");
    const moves = calls.filter((args) => args.some((arg) => arg.includes("move absolute")));
    assert.equal(moves.length, mode === "match" ? 1 : 0, mode);
    if (moves.length)
      assert.equal(moves[0][1], "[con_id=34] move absolute position -536 px 744 px");
  }
});

test("unknown compositors and X11 require no IPC tools; small work areas keep the origin visible", () => {
  const { identify, corner } = require("../../integration/desktop/popout-position.cjs");
  assert.equal(
    identify(
      {},
      () => {
        throw Error("unexpected IPC");
      },
      {},
    ),
    undefined,
  );
  assert.deepEqual(corner({ x: -300, y: 50, width: 10, height: 10 }, 10, 10), { x: -300, y: 50 });
});

test("closing popouts stop being reusable; cancelled closes restore reuse", async () => {
  const { EventEmitter } = require("node:events");
  const { attach, reusable } = require("../../integration/desktop/popout.cjs");
  const window = Object.assign(new EventEmitter(), {
    windowKey: key,
    isDestroyed: () => false,
    webContents: new EventEmitter(),
  });
  attach(window, { dpeVideo: true }, () => {}, screen);
  assert.equal(reusable(window, key), true);
  window.emit("close", { defaultPrevented: false });
  assert.equal(reusable(window, key), false);
  window.webContents.emit("will-prevent-unload", { defaultPrevented: true });
  await new Promise((resolve) => queueMicrotask(resolve));
  assert.equal(reusable(window, key), false, "Ignoring beforeunload still closes the window");
  window.webContents.emit("will-prevent-unload", { defaultPrevented: false });
  await new Promise((resolve) => queueMicrotask(resolve));
  assert.equal(reusable(window, key), true);
  window.emit("close", { defaultPrevented: true });
  await new Promise((resolve) => queueMicrotask(resolve));
  assert.equal(reusable(window, key), true);
  window.isDestroyed = () => true;
  assert.equal(reusable(window, key), false);
  assert.equal(reusable(window, "DISCORD_RTC_DEBUG_POPOUT"), true);
});

test("auto positioning can be disabled independently of floating and video sizing", async () => {
  const { EventEmitter } = require("node:events");
  const { attach } = require("../../integration/desktop/popout.cjs");
  const defaults = windowOptions(options, key, parent, screen, options, {
    popoutAutoPosition: false,
  });
  assert.equal(defaults.dpeAutoPosition, false);
  assert.equal(defaults.dpeFloating, true);
  assert.equal(defaults.dpeAutoSize, true);
  assert.equal(defaults.x, undefined);
  assert.equal(defaults.y, undefined);
  const saved = windowOptions({ ...options, x: 100, y: 100 }, key, parent, screen, options, {
    popoutAutoPosition: false,
  });
  assert.deepEqual([saved.x, saved.y], [100, 100]);
  let size;
  const web = Object.assign(new EventEmitter(), {
    getURL: () => "https://canary.discord.com/popout",
    getZoomFactor: () => 1,
    executeJavaScript: async () => ({ width: 360, height: 640, chromeWidth: 0, chromeHeight: 0 }),
  });
  const window = Object.assign(new EventEmitter(), {
    windowKey: key,
    webContents: web,
    isDestroyed: () => false,
    isMaximized: () => false,
    isFullScreen: () => false,
    getBounds: () => ({ x: 100, y: 120, width: 512, height: 288 }),
    getContentBounds: () => ({ width: 512, height: 288 }),
    setMinimumSize() {},
    setSize: (width, height) => {
      size = { width, height };
    },
    setBounds() {
      assert.fail("Auto sizing must not set position when disabled");
    },
  });
  attach(
    window,
    { dpeVideo: true, dpeAutoSize: true, dpeAutoPosition: false, minWidth: 320, minHeight: 180 },
    () => {},
    screen,
  );
  window.emit("focus");
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(size.width < size.height, "Portrait video still sizes automatically");
});

test("PiP corner preferences validate all four corners and bound initial placement", () => {
  const { normalize } = require("../../integration/desktop/preferences.cjs");
  const { corner } = require("../../integration/desktop/popout-position.cjs");
  assert.equal(normalize().popoutCorner, "bottom-right");
  for (const invalid of [null, false, "center", {}, []])
    assert.equal(normalize({ popoutCorner: invalid }).popoutCorner, "bottom-right");
  const area = { x: -1000, y: 200, width: 800, height: 600 };
  const expected = {
    "top-left": [-984, 216],
    "top-right": [-416, 216],
    "bottom-left": [-984, 684],
    "bottom-right": [-416, 684],
  };
  for (const [value, [x, y]] of Object.entries(expected)) {
    assert.equal(normalize({ popoutCorner: value }).popoutCorner, value);
    assert.deepEqual(corner(area, 200, 100, value), { x, y });
    const result = windowOptions(options, key, parent, screen, options, { popoutCorner: value });
    assert.equal(result.dpeCorner, value);
    assert.deepEqual(
      { x: result.x, y: result.y },
      corner(display.workArea, result.width, result.height, value),
    );
    assert.deepEqual(corner({ x: 20, y: 30, width: 5, height: 5 }, 5, 5, value), { x: 20, y: 30 });
  }
});

test("GNOME maps and sizes without focus while preserving a later user blur", async () => {
  const { EventEmitter } = require("node:events");
  const { attach } = require("../../integration/desktop/popout.cjs");
  for (const mode of ["unfocused", "blurred", "resized"]) {
    let resolve,
      changed,
      resized = false;
    const web = Object.assign(new EventEmitter(), {
      getURL: () => "https://canary.discord.com/popout",
      executeJavaScript: () =>
        new Promise((done) => {
          resolve = done;
        }),
      getZoomFactor: () => 1,
    });
    const window = Object.assign(new EventEmitter(), {
      windowKey: key,
      webContents: web,
      isDestroyed: () => false,
      isFocused: () => false,
      isMaximized: () => false,
      isFullScreen: () => false,
      getBounds: () => ({ x: 50, y: 50, width: resized ? 600 : 512, height: 288 }),
      getContentBounds: () => ({ width: 512, height: 288 }),
      setMinimumSize: () => {},
      setBounds: (bounds) => {
        changed = bounds;
      },
    });
    attach(
      window,
      { dpeVideo: true, dpeReadySizing: true, dpeAutoSize: true, minWidth: 320, minHeight: 180 },
      () => {},
      screen,
    );
    window.emit("ready-to-show");
    window.emit("blur"); // Mutter may emit this before the child has ever had focus.
    if (mode === "blurred") {
      window.emit("focus");
      window.emit("blur");
    }
    if (mode === "resized") {
      resized = true;
      window.emit("resize");
    }
    resolve({ width: 360, height: 640, chromeWidth: 0, chromeHeight: 0 });
    await new Promise((done) => setImmediate(done));
    assert.equal(!!changed, mode === "unfocused", mode);
    assert.equal(window.listenerCount("focus"), 0);
  }
});

test("initial compositor configures and Hyprland's synthetic maximized state permit media sizing", async (t) => {
  const { EventEmitter } = require("node:events");
  const { attach } = require("../../integration/desktop/popout.cjs");
  let resolveMedia, resolvePlacement, changed;
  const position = require("../../integration/desktop/popout-position.cjs");
  t.mock.method(position, "identify", () => ({
    display: async () => display,
    fit: async () => undefined,
    place: () =>
      new Promise((done) => {
        resolvePlacement = done;
      }),
    unchanged: async () => true,
  }));
  const web = Object.assign(new EventEmitter(), {
    getURL: () => "https://canary.discord.com/popout",
    executeJavaScript: () =>
      new Promise((done) => {
        resolveMedia = done;
      }),
    getZoomFactor: () => 1,
  });
  let width = 512;
  const window = Object.assign(new EventEmitter(), {
    windowKey: key,
    webContents: web,
    isDestroyed: () => false,
    isFocused: () => true,
    isMaximized: () => true,
    isFullScreen: () => false,
    getBounds: () => ({ x: 50, y: 50, width, height: 288 }),
    getContentBounds: () => ({ width, height: 288 }),
    setMinimumSize: () => {},
    setBounds: (bounds) => {
      changed = bounds;
    },
  });
  attach(
    window,
    { dpeVideo: true, dpeFloating: true, dpeAutoSize: true, minWidth: 320, minHeight: 180 },
    () => {},
    screen,
  );
  window.emit("focus");
  width += 20;
  window.emit("resize");
  await new Promise((done) => setImmediate(done));
  resolvePlacement();
  await new Promise((done) => setImmediate(done));
  resolveMedia({ width: 360, height: 640, chromeWidth: 0, chromeHeight: 0 });
  await new Promise((done) => setImmediate(done));
  assert.ok(changed.width < changed.height);
  resolvePlacement();
  await new Promise((done) => setImmediate(done));
});
