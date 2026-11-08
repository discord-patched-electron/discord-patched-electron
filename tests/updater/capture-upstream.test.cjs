"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { load } = require("../../integration/video/capture-upstream.cjs");

test("upstream negotiation validates scope and all exports before native mutation", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "dpe-upstream-"));
  const native = Buffer.from("reviewed test module");
  const metadata = {
    schema: 1,
    supported: true,
    voice_sha256: createHash("sha256").update(native).digest("hex"),
    connect: { slot: 100, target: 200, call: 300 },
    update: { slot: 400, target: 500, call: 600 },
  };
  const original = process.dlopen;
  let installs = 0,
    rate = 0,
    mode = 0;
  try {
    fs.writeFileSync(path.join(directory, "discord_voice.node"), native);
    fs.writeFileSync(path.join(directory, "capture-upstream-scope.json"), JSON.stringify(metadata));
    process.dlopen = (addon) => {
      addon.exports = { installCaptureUpstream: () => installs++ };
    };
    assert.throws(() => load(directory, "old-helper"), /unavailable/);
    assert.equal(installs, 0);
    process.dlopen = (addon) => {
      addon.exports = {
        installCaptureUpstreamV2: (...args) => {
          assert.deepEqual(args, [
            path.join(directory, "discord_voice.node"),
            100,
            200,
            300,
            400,
            500,
            600,
            mode,
          ]);
          installs++;
        },
        setCaptureUpstreamRate: (fps) => {
          rate = fps;
        },
        captureUpstreamStats: () => JSON.stringify({ requestedFps: rate }),
      };
    };
    const adapter = load(directory, "helper");
    adapter.setRate(30);
    assert.equal(rate, 60);
    assert.equal(adapter.snapshot().requestedFps, 30);
    adapter.setRate(60);
    assert.equal(rate, 60);
    adapter.setRate(120);
    assert.equal(rate, 120);
    adapter.setRate(0);
    assert.equal(rate, 0);
    assert.equal(adapter.snapshot().requestedFps, 0);
    for (const invalid of [0.5, -1, 241, NaN, "30"])
      assert.throws(() => adapter.setRate(invalid), /integer/);
    assert.equal(rate, 0);
    mode = 1;
    load(directory, "helper", { mode: "runtime" }).setRate(30);
    assert.equal(rate, 30);
    assert.throws(() => load(directory, "helper", { mode: "unknown" }), /Unknown/);
    fs.appendFileSync(path.join(directory, "discord_voice.node"), "changed");
    assert.throws(() => load(directory, "helper"), /reviewed upstream/);
    assert.equal(installs, 2);
  } finally {
    process.dlopen = original;
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
