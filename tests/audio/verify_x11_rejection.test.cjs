"use strict";
// Independent REPRODUCTION and refutation controls. No host /proc, X11 or audio.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function fixture() {
  const calls = [];
  let tick,
    ended,
    missing = false;
  const identities = new Map([
    [100, [1, "1000"]],
    [200, [1, "2000"]],
    [900, [1, "9000"]],
  ]);
  const module = { exports: {} };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "../../integration/audio/app-audio.cjs"), "utf8"),
    {
      module,
      process: { env: { DISPLAY: ":99", DPE_APP_AUDIO: "1" }, pid: 900, getuid: () => 1000 },
      require(name) {
        if (name === "node:fs")
          return {
            statSync: () => ({ uid: 1000 }),
            readFileSync(file) {
              const pid = Number(file.split("/")[2]);
              const [parent, start] = identities.get(pid);
              const fields = Array(20).fill("0");
              fields[0] = "S";
              fields[1] = String(parent);
              fields[19] = start;
              return `${pid} (synthetic app) ${fields.join(" ")}`;
            },
          };
        if (name === "node:child_process")
          return {
            execFileSync(command, args) {
              if (command === "/usr/bin/xprop") {
                if (missing) throw new Error("window gone");
                return `_NET_WM_PID = ${args[1] === "10" ? 100 : 200}\n`;
              }
              assert.equal(command, "/usr/bin/pactl");
              return JSON.stringify(
                [100, 200].map((pid) => ({ properties: { "application.process.id": pid } })),
              );
            },
          };
        throw new Error(`Unexpected require: ${name}`);
      },
      setInterval(callback) {
        tick = callback;
        return { unref() {} };
      },
      clearInterval() {},
    },
  );
  const native = {
    setTransportOptions: (value) => calls.push(value),
    setOnDesktopSourceEnded: (callback) => {
      ended = callback;
    },
    setDesktopSource() {},
    setDesktopSourceWithOptions() {},
    clearDesktopSource() {},
    destroy() {},
  };
  const adapter = module.exports.wrap(native, { warn() {} });
  adapter.setDesktopSource("window:10:0", false, "window");
  adapter.setTransportOptions({ soundsharePid: 1 });
  assert.equal(calls.at(-1).soundsharePid, 100);
  return {
    adapter,
    native,
    calls,
    tick: () => tick(),
    ended: () => ended(),
    disappear: () => {
      missing = true;
    },
  };
}

for (const method of ["setDesktopSource", "setDesktopSourceWithOptions"]) {
  test(`REPRODUCTION with real resolver: ${method} rejection and cleanup controls`, () => {
    const f = fixture();
    const failure = new Error("injected native rejection");
    f.native[method] = () => {
      // Selection has already activated B before calling the native setter.
      assert.equal(f.calls.at(-1).soundsharePid, 200);
      throw failure;
    };
    const args =
      method === "setDesktopSource"
        ? ["window:20:0", false, "window"]
        : [{ sourceId: "window:20:0", type: "window" }];
    assert.throws(() => f.adapter[method](...args), failure);
    for (let index = 0; index < 3; index++) f.tick();
    f.adapter.setTransportOptions({ encodingBitRate: 64000 });
    assert.equal(f.calls.at(-1).soundsharePid, 200);
    f.ended();
    f.adapter.setTransportOptions({ encodingBitRate: 64000 });
    assert.equal(
      f.calls.findLast((value) => Object.hasOwn(value, "soundsharePid")).soundsharePid,
      0,
    );
    f.adapter.destroy();
  });
}

test("refutation control: vanished X11 window stops the rejected PID on the next tick", () => {
  const f = fixture();
  f.native.setDesktopSource = () => {
    throw new Error("injected native rejection");
  };
  assert.throws(() => f.adapter.setDesktopSource("window:20:0", false, "window"));
  f.disappear();
  f.tick();
  assert.equal(f.calls.findLast((value) => Object.hasOwn(value, "soundsharePid")).soundsharePid, 0);
  f.adapter.destroy();
});
