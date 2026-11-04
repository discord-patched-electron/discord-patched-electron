"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { load } = require("../../integration/video/capture-sync.cjs");

test("Synchronization validates hash, offsets and all exports before native mutation", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "dpe-sync-"));
  const native = Buffer.from("reviewed synchronization module");
  const metadata = {
    schema: 1,
    supported: true,
    voice_sha256: createHash("sha256").update(native).digest("hex"),
    call: 100,
    target: 200,
  };
  const original = process.dlopen;
  let installs = 0;
  const write = () =>
    fs.writeFileSync(path.join(directory, "capture-sync-scope.json"), JSON.stringify(metadata));
  try {
    fs.writeFileSync(path.join(directory, "discord_voice.node"), native);
    write();
    process.dlopen = (addon) => {
      addon.exports = { installCaptureSync: () => installs++ };
    };
    assert.throws(() => load(directory, "old-helper"), /unavailable/);
    assert.equal(installs, 0);
    process.dlopen = (addon) => {
      addon.exports = {
        installCaptureSync: (...args) => {
          assert.deepEqual(args, [path.join(directory, "discord_voice.node"), 100, 200]);
          installs++;
        },
        captureSyncStats: () => JSON.stringify({ installed: true, correctedWaits: 1 }),
      };
    };
    assert.equal(load(directory, "helper").snapshot().correctedWaits, 1);
    metadata.call = 1.5;
    write();
    assert.throws(() => load(directory, "helper"), /reviewed capture synchronization/);
    metadata.call = 100;
    write();
    fs.appendFileSync(path.join(directory, "discord_voice.node"), "changed");
    assert.throws(() => load(directory, "helper"), /reviewed capture synchronization/);
    assert.equal(installs, 1);
  } finally {
    process.dlopen = original;
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
