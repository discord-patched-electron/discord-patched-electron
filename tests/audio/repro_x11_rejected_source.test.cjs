"use strict";
// Regression: rejected native selection stays silent across later callbacks.
// Reuse the native mock and window/PID map from adapter.test.cjs. Timer and
// process-identity boundaries are mocked; no X server, /proc or audio is read.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { wrap } = require("../../integration/audio/app-audio.cjs");

function fixture(t) {
  const calls = [];
  const native = {
    setTransportOptions: (value) => calls.push(value),
    setDesktopSource() {},
    setDesktopSourceWithOptions() {},
    clearDesktopSource() {},
    destroy() {},
    setOnDesktopSourceEnded() {},
  };
  const resolved = new Map([
    ["window:10:0", { pid: 100, start: "1" }],
    ["window:20:0", { pid: 200, start: "2" }],
  ]);
  let tick;
  t.mock.method(global, "setInterval", (callback) => {
    tick = callback;
    return { unref() {} };
  });
  t.mock.method(global, "clearInterval", () => {});
  const adapter = wrap(native, {
    enabled: true,
    wayland: false,
    resolvePid: (source) => resolved.get(source.id),
    identity: (pid) => [...resolved.values()].find((value) => value.pid === pid),
    warn() {},
  });
  t.after(() => adapter.destroy());
  return { adapter, native, calls, tick: () => tick() };
}

for (const method of ["setDesktopSource", "setDesktopSourceWithOptions"]) {
  test(`X11 ${method} rejection silences rejected app audio`, (t) => {
    const f = fixture(t);
    f.adapter.setDesktopSource("window:10:0", false, "window");
    f.adapter.setTransportOptions({ soundsharePid: 1 });
    assert.equal(f.calls.at(-1).soundsharePid, 100);
    const rejection = new Error("native source unavailable");
    f.native[method] = () => {
      throw rejection;
    };
    const args =
      method === "setDesktopSource"
        ? ["window:20:0", false, "window"]
        : [{ sourceId: "window:20:0", type: "window" }];
    assert.throws(() => f.adapter[method](...args), rejection);
    // Native rejection must never commit B's audio selection.
    assert.equal(
      f.calls.findLast((value) => Object.hasOwn(value, "soundsharePid")).soundsharePid,
      0,
    );
    f.tick();
    assert.equal(
      f.calls.findLast((value) => Object.hasOwn(value, "soundsharePid")).soundsharePid,
      0,
    );
    f.adapter.setTransportOptions({ encodingBitRate: 64000 });
    assert.equal(
      f.calls.findLast((value) => Object.hasOwn(value, "soundsharePid")).soundsharePid,
      0,
    );
    // Positive control: an explicit clear does silence capture.
    f.adapter.clearDesktopSource();
    assert.equal(
      f.calls.findLast((value) => Object.hasOwn(value, "soundsharePid")).soundsharePid,
      0,
    );
  });
}
