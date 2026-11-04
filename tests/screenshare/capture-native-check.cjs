"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");
const { load } = require("../../integration/video/capture.cjs");
const [directory, helper, mode] = process.argv.slice(2);
const filename = path.join(directory, "discord_voice.node");
const fixture = require(filename);
const metadata = {
  schema: 1,
  supported: true,
  voice_sha256: createHash("sha256").update(fs.readFileSync(filename)).digest("hex"),
  ...JSON.parse(fixture.describe()),
};
fs.writeFileSync(path.join(directory, "capture-scope.json"), JSON.stringify(metadata));
const adapter = load(directory, helper, { mode });
adapter.setRate(60);
if (mode === "paced") {
  assert.equal(adapter.snapshot().pacingFps, 60);
  for (const value of [-1, 241, 1.5, NaN, Infinity, "30"])
    assert.throws(() => adapter.setRate(value), /integer/);
}
const result = JSON.parse(fixture.run());
const counters = adapter.snapshot();
assert.equal(counters.installed, true);
assert.equal(counters.frames, 1);
assert.equal(counters.dmabufFrames, 1);
assert.equal(
  counters.processingUsLog2.reduce((sum, value) => sum + value, 0),
  1,
);
if (mode !== "observe") {
  assert.equal(counters.latestEnabled, true);
  assert.deepEqual(result, { selected: 3, released: 4, dequeued: 4 });
  assert.equal(counters.skippedFrames, 3);
} else {
  assert.deepEqual(result, { selected: 0, released: 1, dequeued: 1 });
  assert.equal(counters.skippedFrames, 0);
}
// Reinstall is idempotent and never patches the newly inserted calls again.
assert.equal(load(directory, helper, { mode }).snapshot().installed, true);
adapter.setRate(0);
assert.equal(adapter.snapshot().pacingFps, 0);
metadata.voice_sha256 = "0".repeat(64);
fs.writeFileSync(path.join(directory, "capture-scope.json"), JSON.stringify(metadata));
assert.throws(() => load(directory, helper, { mode }), /reviewed adapter/);
console.log(JSON.stringify({ mode, result, counters }));
