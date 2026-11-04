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
  );
  assert.deepEqual([saved.x, saved.y, saved.width, saved.height], [-1800, 100, 640, 360]);
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
  for (const cancel of [false, true]) {
    let resolve, changed;
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
      getBounds: () => ({ x: 50, y: 50, width: 512, height: 288 }),
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
    if (cancel) window.emit("resize");
    resolve({ width: 1080, height: 1920, chromeWidth: 0, chromeHeight: 0 });
    await new Promise((done) => setImmediate(done));
    if (cancel) assert.equal(changed, undefined);
    else {
      assert.ok(changed.width < changed.height);
      assert.equal(changed.y + changed.height, 704);
      assert.equal(changed.x + changed.width, 1264);
    }
    assert.equal(window.listenerCount("resize"), 0);
  }
});

test("niri placement targets a verified child ID and leaves unrelated or retiled windows alone", async () => {
  const { identify } = require("../../integration/desktop/popout-position.cjs");
  for (const mode of ["match", "foreign", "retiled", "focus-moved"]) {
    const calls = [];
    let count = 0;
    const run = async (_command, args) => {
      calls.push(args);
      return {
        stdout: JSON.stringify([
          {
            id: 19,
            pid: mode === "foreign" ? process.pid + 1 : process.pid,
            title: "Discord Popout",
            is_focused: true,
            is_floating: mode !== "retiled" || count++ === 0,
            layout: { tile_size: [512, 288] },
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
        "752",
        "--y",
        "386",
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
