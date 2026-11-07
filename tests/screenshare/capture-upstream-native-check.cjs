"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const [directory, helper, mode] = process.argv.slice(2);
const filename = path.join(directory, "discord_voice.node");
const fixture = require(filename);
const metadata = JSON.parse(fixture.describe());
const addon = { exports: {} };
process.dlopen(addon, helper);
const api = addon.exports;
const offsets = (value) =>
  [value.connect, value.update].flatMap((entry) => [entry.slot, entry.target, entry.call]);
for (const mutate of [
  (m) => m.connect.slot++,
  (m) => m.connect.target++,
  (m) => m.connect.call++,
  (m) => m.update.target++,
  (m) => (m.update.call = m.connect.call),
  (m) => (m.update.call = 0xfffffff0),
]) {
  const invalid = structuredClone(metadata);
  mutate(invalid);
  assert.throws(
    () => api.installCaptureUpstreamV2(filename, ...offsets(invalid), Number(mode)),
    /capture|native|linkage/i,
  );
  assert.equal(JSON.parse(api.captureUpstreamStats()).installed, false);
  assert.deepEqual(JSON.parse(fixture.describe()).calls, [255, 255, 255, 255]);
}
api.installCaptureUpstreamV2(filename, ...offsets(metadata), Number(mode));
api.installCaptureUpstreamV2(filename, ...offsets(metadata), Number(mode));
assert.throws(
  () => api.installCaptureUpstreamV2(filename, ...offsets(metadata), 1 - Number(mode)),
  /changed/,
);
assert.deepEqual(JSON.parse(fixture.describe()).calls, [232, 232, 255, 255]);
assert.equal(JSON.parse(fixture.describe()).slotsIntact, true);
api.setCaptureUpstreamRate(60);
for (const fps of [-1, 241, 1.5, NaN, Infinity, "30"])
  assert.throws(() => api.setCaptureUpstreamRate(fps), /integer/);
const stats = JSON.parse(api.captureUpstreamStats());
assert.equal(stats.installed, true);
assert.equal(stats.mode, Number(mode) ? "runtime" : "initial");
assert.equal(stats.requestedFps, 60);
assert.equal(stats.activeStreams, 0);
assert.deepEqual(JSON.parse(fixture.run()), { connects: 2, updates: 2 });
console.log(JSON.stringify({ stats, fixture: JSON.parse(fixture.describe()) }));
