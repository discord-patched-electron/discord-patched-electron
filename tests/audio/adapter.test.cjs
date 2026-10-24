const { test } = require("node:test");
const assert = require("node:assert/strict");
const { wrap } = require("../../integration/audio/app-audio.cjs");
function fixture() {
  const calls = [];
  let end;
  const native = {
    setTransportOptions: (value) => calls.push(value),
    setDesktopSource() {},
    setDesktopSourceWithOptions() {},
    clearDesktopSource() {},
    destroy() {},
    setOnDesktopSourceEnded: (value) => {
      end = value;
    },
    setSelfMute(value) {
      assert.equal(this, native);
      calls.push({ selfMute: value });
    },
  };
  const resolved = new Map([
    ["window:10:0", { pid: 100, start: "1" }],
    ["window:20:0", { pid: 200, start: "2" }],
  ]);
  const adapter = wrap(native, {
    enabled: true,
    wayland: false,
    resolvePid: (source) => resolved.get(source.id),
    warn() {},
  });
  return { adapter, native, calls, end: () => end?.("ended") };
}

test("window selection replaces system PID, switches, disables and preserves unrelated options", () => {
  const { adapter, calls } = fixture();
  adapter.setTransportOptions({
    soundsharePid: 1,
    soundshareLoopback: true,
    encodingBitRate: 128000,
  });
  assert.equal(calls.at(-1).soundsharePid, 0);
  adapter.setDesktopSourceWithOptions({ sourceId: "window:10:0", type: "window" });
  assert.deepEqual(calls.at(-1), { soundsharePid: 100, soundshareLoopback: false });
  adapter.setTransportOptions({ encodingBitRate: 64000 });
  assert.equal(calls.at(-1).encodingBitRate, 64000);
  adapter.setDesktopSource("window:20:0", false, "window");
  assert.equal(calls.at(-1).soundsharePid, 200);
  adapter.setTransportOptions({ soundsharePid: 0 });
  assert.equal(calls.at(-1).soundsharePid, 0);
  adapter.setSelfMute(true);
  assert.deepEqual(calls.at(-1), { selfMute: true });
  adapter.destroy();
});

test("unresolved windows cannot fall back to system capture; explicit screens retain it", () => {
  const { adapter, calls } = fixture();
  adapter.setDesktopSource("window:99:0", false, "window");
  adapter.setTransportOptions({ soundsharePid: 1, soundshareLoopback: true });
  assert.equal(calls.at(-1).soundsharePid, 0);
  adapter.setDesktopSource("screen:0:0", false, "screen");
  assert.deepEqual(calls.at(-1), { soundsharePid: 1, soundshareLoopback: true });
  adapter.clearDesktopSource();
  assert.equal(calls.at(-1).soundsharePid, 0);
  adapter.destroy();
});

test("source-ended callback stops capture and still reaches the client", () => {
  const f = fixture();
  let called = false;
  f.adapter.setOnDesktopSourceEnded(() => {
    called = true;
  });
  f.adapter.setDesktopSource("window:10:0", false, "window");
  f.adapter.setTransportOptions({ soundsharePid: 1 });
  f.end();
  assert.equal(f.calls.at(-1).soundsharePid, 0);
  assert.equal(called, true);
  f.adapter.destroy();
});

test("explicit disable preserves the upstream instance", () => {
  const native = {};
  assert.equal(wrap(native, { enabled: false }), native);
});

test("trace-only preserves native arguments and callbacks without logging secrets", () => {
  const lines = [];
  let received, callback;
  const native = {
    setTransportOptions(options) {
      assert.equal(this, native);
      received = options;
      return 42;
    },
    setDesktopSourceWithOptions(options) {
      received = options;
    },
    presentDesktopSourcePicker(style) {
      received = style;
    },
    setOnSoundshareFailed(value) {
      callback = value;
    },
  };
  const adapter = wrap(native, {
    enabled: false,
    trace: true,
    traceWrite: (line) => lines.push(line),
  });
  const options = {
    soundsharePid: 1,
    soundshareLoopback: true,
    encryptionKey: "secret-key",
    endpoint: "secret-address",
    soundshareId: "private-source-title",
  };
  assert.equal(adapter.setTransportOptions(options), 42);
  assert.equal(received, options);
  const selection = {
    sourceId: "private-window-title",
    type: "window",
    privateOption: "private-option",
  };
  adapter.setDesktopSourceWithOptions(selection);
  assert.equal(received, selection);
  adapter.presentDesktopSourcePicker(2);
  assert.equal(received, 2);
  const result = { privateState: "private-callback" };
  let forwarded;
  adapter.setOnSoundshareFailed((value) => {
    forwarded = value;
    return 19;
  });
  assert.equal(callback(result), 19);
  assert.equal(forwarded, result);
  assert.match(lines.join("\n"), /"soundsharePid":1/);
  assert.match(lines.join("\n"), /setOnSoundshareFailed:callback/);
  assert.doesNotMatch(lines.join("\n"), /secret-|private-|encryptionKey|endpoint/);
  const count = lines.length;
  adapter.setTransportOptions({ encryptionKey: "secret-key" });
  assert.equal(lines.length, count);
});

test("trace failure cannot change a native return value or swallow native errors", () => {
  const error = new Error("native failure");
  const adapter = wrap(
    {
      clearDesktopSource: () => 27,
      destroy: () => {
        throw error;
      },
    },
    {
      enabled: false,
      trace: true,
      traceWrite: () => {
        throw new Error("logger failure");
      },
    },
  );
  assert.equal(adapter.clearDesktopSource(), 27);
  assert.throws(
    () => adapter.destroy(),
    (value) => value === error,
  );
});

test("Wayland portal tokens never resolve through the Xwayland display", () => {
  const { windowPid } = require("../../integration/audio/app-audio.cjs");
  const previous = process.env.WAYLAND_DISPLAY;
  try {
    process.env.WAYLAND_DISPLAY = "wayland-fixture";
    assert.equal(windowPid({ id: "window:10:0" }), null);
  } finally {
    if (previous === undefined) delete process.env.WAYLAND_DISPLAY;
    else process.env.WAYLAND_DISPLAY = previous;
  }
});
