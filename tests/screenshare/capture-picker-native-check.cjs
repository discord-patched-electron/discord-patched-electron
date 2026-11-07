"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const [directory, helper] = process.argv.slice(2);
const filename = path.join(directory, "discord_voice.node");
const fixture = require(filename);
const metadata = JSON.parse(fixture.describe());
const addon = { exports: {} };
process.dlopen(addon, helper);
const api = addon.exports;
const events = [];
const callback = (json) => events.push(JSON.parse(json));
for (const mutate of [
  (o) => o[0]++,
  (o) => o[1]++,
  (o) => (o[3] = o[5]),
  (o) => o[6]++,
  (o) => o[7]++,
  (o) => (o[8] = 0xfffffff0),
  (o) => o[11]++,
  (o) => (o[12] = o[13]),
]) {
  const invalid = [...metadata.offsets];
  mutate(invalid);
  assert.throws(
    () => api.installCapturePicker(filename, ...invalid, callback),
    /capture|native|slot/i,
  );
  assert.equal(JSON.parse(fixture.describe()).intact, true);
}
assert.throws(() => api.installCapturePicker(filename, ...metadata.offsets, null), /callback/);
assert.equal(JSON.parse(fixture.describe()).intact, true);
api.installCapturePicker(filename, ...metadata.offsets, callback);
api.installCapturePicker(filename, ...metadata.offsets, callback);
const changed = [...metadata.offsets];
changed[7]++;
assert.throws(() => api.installCapturePicker(filename, ...changed, callback), /changed/);
assert.equal(JSON.parse(fixture.describe()).intact, false);
assert.equal(api.installCaptureWake(filename, ...Array(10).fill(1)), false);
assert.equal(fixture.run(), 0);
const wait = async (count) => {
  const until = Date.now() + 1000;
  while (events.length < count && Date.now() < until)
    await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(events.length, count);
};
(async () => {
  await wait(1);
  assert.equal(events[0].event, "request");
  assert.equal(events[0].sourceType, 1);
  assert.equal(api.cancelCapturePicker(events[0].requestId), true);
  assert.equal(api.cancelCapturePicker(events[0].requestId), false);
  await wait(2);
  assert.equal(events[1].event, "cancelled");
  assert.equal(JSON.parse(fixture.describe()).callbacks, 1);
  for (const args of [
    [0, "cancel", "", 0],
    [1, "invalid", "", 0],
    [1, "cancel", "", 0xffffffff],
    [1, "cancel", "\0", 0],
  ])
    assert.throws(() => api.finishCapturePicker(...args), /picker/);
  console.log("reviewed picker linkage, rejected scopes, real TSFN events and cancellation passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
