"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { load } = require("../../integration/video/capture-picker.cjs");

test("external picker validates its module, endpoints and request lifecycle", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "dpe-picker-"));
  const native = Buffer.from("reviewed test module");
  const metadata = {
    schema: 1,
    abi: 3,
    supported: true,
    voice_sha256: createHash("sha256").update(native).digest("hex"),
    show: { slot: 100, target: 200 },
    new: { slot: 300, target: 400 },
    destroy: { slot: 500, target: 600 },
    selection: { mutex: 700, config: 800, present: 900 },
    update: { slot: 1000, target: 1100 },
    delivery: { clone: 1200, release: 1300, destroy: 1400 },
  };
  const original = process.dlopen;
  let installs = 0,
    wakeInstalls = 0,
    callback;
  const completions = [],
    cancellations = [],
    events = [];
  try {
    fs.writeFileSync(path.join(directory, "discord_voice.node"), native);
    fs.writeFileSync(path.join(directory, "capture-picker-scope.json"), JSON.stringify(metadata));
    process.dlopen = (addon) => {
      addon.exports = {};
    };
    assert.throws(() => load(directory, "old-helper", () => {}), /unavailable/);
    process.dlopen = (addon) => {
      addon.exports = {
        installCapturePicker: (...args) => {
          callback = args.pop();
          assert.deepEqual(args, [
            path.join(directory, "discord_voice.node"),
            100,
            200,
            300,
            400,
            500,
            600,
            700,
            800,
            900,
            1000,
            1100,
            1200,
            1300,
            1400,
          ]);
          installs++;
        },
        installCaptureWake: (...args) => {
          assert.equal(args.length, 11);
          wakeInstalls++;
          return true;
        },
        finishCapturePicker: (...args) => {
          completions.push(args);
          return true;
        },
        cancelCapturePicker: (id) => {
          cancellations.push(id);
          return true;
        },
      };
    };
    const picker = load(directory, "helper", (event) => events.push(event));
    assert.equal(picker.eventDelivery, false);
    assert.equal(wakeInstalls, 0);
    callback("malformed");
    callback(JSON.stringify({ event: "other", requestId: 1, sourceType: 0 }));
    callback(JSON.stringify({ event: "request", requestId: 0, sourceType: 0 }));
    callback(JSON.stringify({ event: "request", requestId: 1, sourceType: 0 }));
    assert.equal(events.length, 1);
    assert.equal(picker.finish(1, "selected", "/run/user/1000/pipewire-0", 42), true);
    assert.equal(picker.cancel(1), true);
    assert.deepEqual(completions, [[1, "selected", "/run/user/1000/pipewire-0", 42]]);
    assert.deepEqual(cancellations, [1]);
    for (const bad of [0, -1, 0.5, NaN, 0x100000000, "1"])
      assert.throws(() => picker.cancel(bad), /Invalid/);
    for (const socket of ["relative", "/with\0nul", "/" + "x".repeat(108)])
      assert.throws(() => picker.finish(1, "selected", socket, 42), /Invalid/);
    for (const node of [-1, 0.5, NaN, 0xffffffff, "42"])
      assert.throws(() => picker.finish(1, "selected", "/run/pw", node), /Invalid/);
    assert.throws(() => picker.finish(1, "unknown"), /Invalid/);
    metadata.wake = { abi: 1 };
    for (const name of ["read", "gpu", "cpu", "receive", "overdue"])
      metadata.wake[name] = { call: 1500, target: 1600 };
    fs.writeFileSync(path.join(directory, "capture-picker-scope.json"), JSON.stringify(metadata));
    assert.equal(load(directory, "helper", () => {}).eventDelivery, true);
    assert.equal(wakeInstalls, 1);
    metadata.wake.read.call = -1;
    fs.writeFileSync(path.join(directory, "capture-picker-scope.json"), JSON.stringify(metadata));
    assert.equal(load(directory, "helper", () => {}).eventDelivery, false);
    assert.equal(wakeInstalls, 1);
    fs.appendFileSync(path.join(directory, "discord_voice.node"), "changed");
    assert.throws(() => load(directory, "helper", () => {}), /reviewed/);
    assert.equal(installs, 3);
  } finally {
    process.dlopen = original;
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
