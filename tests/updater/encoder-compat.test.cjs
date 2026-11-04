"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { load } = require("../../integration/video/encoder.cjs");

test("encoder guard verifies the module before installing exact reviewed slots", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "dpe-encoder-"));
  const original = process.dlopen;
  const native = Buffer.from("reviewed test module");
  const metadata = {
    schema: 1,
    abi: 1,
    decoder_hardware: { electron: true, dav1d: false, libvpx: false },
    supported: true,
    voice_sha256: createHash("sha256").update(native).digest("hex"),
    create: { slot: 100, target: 200, lazy: 250 },
    update: { slot: 300, target: 400, lazy: 450 },
    destroy: { slot: 500, target: 600, lazy: 650 },
    vendor: { target: 700 },
    av1_sequence: { target: 800, calls: [900, 1000] },
    amd_quality: { slot: 1100, target: 1200, query: 1300 },
    allocation: {
      rate_call: 2800,
      rate_target: 2900,
      budget_call: 3000,
      budget_target: 3100,
      destructors: [
        { slot: 3200, target: 3300 },
        { slot: 3400, target: 3500 },
      ],
    },
    stream_limit: { call: 3600, target: 3700, info_destructor: 3800 },
    transitions: {
      quality: { target: 1800, calls: [1900, 2000, 2100] },
      bandwidth: { target: 2200, calls: [2300, 2400, 2500] },
      started: 2600,
      codec: 2700,
    },
    qp_trust: { slot: 1400, target: 1500, bandwidth_call: 1600, config_target: 1700 },
  };
  const write = () =>
    fs.writeFileSync(path.join(directory, "encoder-scope.json"), JSON.stringify(metadata));
  let loaded = 0;
  try {
    fs.writeFileSync(path.join(directory, "discord_voice.node"), native);
    write();
    process.dlopen = (addon, helper) => {
      loaded++;
      assert.equal(helper, "helper");
      addon.exports = {
        installEncoderGuard: (...args) =>
          assert.deepEqual(args, [
            path.join(directory, "discord_voice.node"),
            100,
            200,
            250,
            300,
            400,
            450,
            500,
            600,
            650,
            700,
          ]),
        encoderGuardStats: () => '{"reason":"amd-av1-coded-size-padding"}',
        installAv1Levels: (...args) =>
          assert.deepEqual(args, [path.join(directory, "discord_voice.node"), 800, 900, 1000]),
        installEncoderAllocation: (...args) =>
          assert.deepEqual(args, [
            path.join(directory, "discord_voice.node"),
            2800,
            2900,
            3000,
            3100,
            3200,
            3300,
            3400,
            3500,
          ]),
        installStreamBitrateEnvelope: (...args) =>
          assert.deepEqual(args, [path.join(directory, "discord_voice.node"), 3600, 3700, 3800]),
        installScalerTransitions: (...args) =>
          assert.deepEqual(args, [
            path.join(directory, "discord_voice.node"),
            1800,
            1900,
            2000,
            2100,
            2200,
            2300,
            2400,
            2500,
            2600,
            2700,
          ]),
        installEncoderQpTrust: (...args) =>
          assert.deepEqual(args, [
            path.join(directory, "discord_voice.node"),
            1400,
            1500,
            1600,
            1700,
          ]),
        installEncoderQuality: (...args) =>
          assert.deepEqual(args, [path.join(directory, "discord_voice.node"), 1100, 1200, 1300]),
      };
    };
    const adapter = load(directory, "helper");
    assert.equal(adapter.snapshot().reason, "amd-av1-coded-size-padding");
    assert.equal(adapter.decoderHardware("electron"), true);
    assert.equal(adapter.decoderHardware("dav1d"), false);
    assert.equal(adapter.decoderHardware("future"), null);
    metadata.update.slot = -1;
    write();
    assert.throws(() => load(directory, "helper"), /reviewed compatibility guard/);
    metadata.update.slot = 300;
    write();
    fs.appendFileSync(path.join(directory, "discord_voice.node"), "tampered");
    assert.throws(() => load(directory, "helper"), /reviewed compatibility guard/);
    assert.equal(loaded, 1);
  } finally {
    process.dlopen = original;
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
