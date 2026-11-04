"use strict";
const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

function fixture(failure, upstreamEnabled, env = {}) {
  const native = Buffer.from("reviewed test module");
  const modes = [],
    calls = [],
    errors = [];
  const metadata = {
    schema: 1,
    supported: true,
    voice_sha256: createHash("sha256").update(native).digest("hex"),
    dequeue: { call: 1, target: 2 },
    release: { call: 3, target: 4 },
  };
  const upstream = {
    setRate(fps) {
      calls.push(["upstream", fps]);
      if (failure === "rate" && fps) throw new Error("rejected update");
    },
    snapshot() {
      if (failure === "snapshot") throw new Error("unavailable stats");
      return { installed: true };
    },
  };
  const context = {
    module: { exports: {} },
    require(name) {
      if (name === "node:fs")
        return {
          readFileSync: (file) => (file.endsWith(".json") ? JSON.stringify(metadata) : native),
        };
      if (name === "./dpe-capture-upstream.cjs")
        return {
          load(_directory, _helper, options) {
            modes.push(options.mode);
            if (failure === "install") throw new Error("unreviewed linkage");
            return upstream;
          },
        };
      return require(name);
    },
    process: {
      env,
      dlopen(addon) {
        addon.exports = {
          installCapture() {},
          captureStats: () => JSON.stringify({ installed: true, frames: 5 }),
          setCaptureRate(fps) {
            calls.push(["downstream", fps]);
            return 17;
          },
        };
      },
    },
    console: { error: (...args) => errors.push(args.join(" ")) },
  };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "../../integration/video/capture.cjs"), "utf8"),
    context,
  );
  return {
    adapter: context.module.exports.load("/voice", "/helper", {
      mode: "paced",
      ...(upstreamEnabled === undefined ? {} : { upstreamEnabled }),
    }),
    calls,
    modes,
    errors,
  };
}

for (const failure of ["install", "rate", "snapshot"]) {
  test(`optional upstream ${failure} failure preserves capture and restores its offers`, () => {
    const { adapter, calls, errors } = fixture(failure, true);
    assert.equal(adapter.setRate(60), 17);
    assert.equal(adapter.snapshot().installed, true);
    assert.equal(adapter.snapshot().upstream, null);
    assert.equal(adapter.setRate(30), 17);
    assert.deepEqual(
      calls.filter(([target]) => target === "downstream"),
      [
        ["downstream", 60],
        ["downstream", 30],
      ],
    );
    const restores = calls.filter(([target, fps]) => target === "upstream" && fps === 0);
    assert.equal(restores.length, failure === "install" ? 0 : 1);
    assert.equal(errors.length, 1);
  });
}

test("explicitly disabled producer preferences leave only downstream pacing", () => {
  const { adapter, calls, errors } = fixture("install", false);
  assert.equal(adapter.setRate(60), 17);
  assert.equal(adapter.snapshot().upstream, null);
  assert.deepEqual(calls, [["downstream", 60]]);
  assert.deepEqual(errors, []);
});

test("default pacing uses only initial preferences; runtime changes require opt-in", () => {
  assert.deepEqual(fixture(null).modes, ["initial"]);
  assert.deepEqual(fixture(null, undefined, { DPE_CAPTURE_UPSTREAM: "1" }).modes, ["runtime"]);
  assert.deepEqual(fixture(null, undefined, { DPE_CAPTURE_UPSTREAM: "0" }).modes, []);
});
