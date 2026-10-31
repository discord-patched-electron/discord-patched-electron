"use strict";
// Independent native-boundary controls; no native calls, /proc or audio services.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { wrap } = require("../../integration/audio/app-audio.cjs");

for (const method of ["setDesktopSource", "setDesktopSourceWithOptions"]) {
  for (const kind of ["window", "screen"]) {
    test(`snapshot review: ${method}, repeated ${kind} rejection clears intent and timer`, (t) => {
      const calls = [];
      const timers = new Set();
      let reject = false;
      t.mock.method(global, "setInterval", (callback) => {
        const timer = { callback, unref() {} };
        timers.add(timer);
        return timer;
      });
      t.mock.method(global, "clearInterval", (timer) => timers.delete(timer));
      const failure = new Error("native fixture rejection");
      const native = {
        setTransportOptions: (options) => calls.push(options),
        setOnDesktopSourceEnded() {},
        [method]() {
          if (reject) throw failure;
          return 42;
        },
        destroy() {},
      };
      const adapter = wrap(native, {
        wayland: false,
        enabled: true,
        resolvePid: () => ({ pid: 500, start: "fixture" }),
        identity: () => ({ pid: 500, start: "fixture" }),
        warn() {},
      });
      t.after(() => adapter.destroy());
      const args =
        method === "setDesktopSource"
          ? [`${kind}:10:0`, false, kind]
          : [{ sourceId: `${kind}:10:0`, type: kind }];
      assert.equal(adapter[method](...args), 42);
      adapter.setTransportOptions({ soundsharePid: 1 });
      assert.equal(calls.at(-1).soundsharePid, kind === "window" ? 500 : 1);
      reject = true;
      // Exercise the same-source short circuit, not only switching A to B.
      assert.throws(
        () => adapter[method](...args),
        (error) => error === failure,
      );
      assert.equal(calls.at(-1).soundsharePid, 0);
      assert.equal(calls.at(-1).soundshareLoopback, false);
      assert.equal(timers.size, 0);
      reject = false;
      // A successful video retry alone must not resurrect the rejected audio intent.
      assert.equal(adapter[method](...args), 42);
      assert.equal(calls.at(-1).soundsharePid, 0);
      adapter.setTransportOptions({ encodingBitRate: 64000 });
      assert.equal(calls.at(-1).encodingBitRate, 64000);
      adapter.setTransportOptions({ soundsharePid: 1 });
      assert.equal(calls.at(-1).soundsharePid, kind === "window" ? 500 : 1);
      adapter.destroy();
      assert.equal(timers.size, 0);
      assert.equal(calls.at(-1).soundsharePid, 0);
    });
  }
}
