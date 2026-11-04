"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { drawMarker, readMarker, distribution, FrameMeasurements } = require("./frame-markers.cjs");

function marker(sequence, width = 640, height = 360) {
  const image = { width, height, data: new Uint8ClampedArray(width * height * 4) };
  const context = {
    fillStyle: "black",
    fillRect(x, y, w, h) {
      // Simulate limited-range output instead of requiring exact black/white.
      const value = this.fillStyle === "white" ? 235 : 16;
      for (let row = y; row < y + h; row++)
        for (let column = x; column < x + w; column++) {
          const offset = (row * width + column) * 4;
          image.data.set([value, value, value, 255], offset);
        }
    },
  };
  drawMarker(context, width, height, sequence);
  return image;
}

test("frame markers survive supported resolution changes and sequence byte boundaries", () => {
  for (const [width, height] of [
    [640, 360],
    [320, 180],
    [426, 240],
    [1280, 720],
  ])
    for (const sequence of [0, 1, 255, 256, 65535, 65536, 0xffffff])
      assert.equal(readMarker(marker(sequence, width, height)), sequence);
});

test("partial, gray and corrupted markers never count as valid frames", () => {
  assert.equal(readMarker({ width: 640, height: 360, data: new Uint8Array(10) }), null);
  const gray = marker(1);
  gray.data.fill(127);
  assert.equal(readMarker(gray), null);
  const corrupted = marker(1);
  // Invert a payload cell in both rows: contrast passes, CRC must reject it.
  const x = Math.floor((10.5 * corrupted.width) / 40);
  for (const row of [0, 1]) {
    const y = Math.floor((row + 0.5) * corrupted.height * 0.06);
    const offset = (y * corrupted.width + x) * 4;
    for (let channel = 0; channel < 3; channel++)
      corrupted.data[offset + channel] = 251 - corrupted.data[offset + channel];
  }
  assert.equal(readMarker(corrupted), null);
});

test("receiver measurements separate duplicate, stale, unknown and damaged frames", () => {
  let now = 0;
  const metrics = new FrameMeasurements({ now: () => now });
  metrics.submitted(1, "initial");
  now = 10;
  metrics.submitted(2, "initial");
  now = 30;
  metrics.received(marker(2));
  now = 35;
  metrics.received(marker(2));
  now = 40;
  metrics.received(marker(1));
  metrics.received(marker(3));
  metrics.received({});
  now = 50;
  metrics.submitted(3, "recovery");
  now = 80;
  metrics.received(marker(3, 320, 180));
  const report = metrics.report();
  assert.equal(report.duplicates, 1);
  assert.equal(report.outOfOrder, 1);
  assert.equal(report.unknownMarkers, 1);
  assert.equal(report.invalidMarkers, 1);
  assert.deepEqual(
    report.frames.map((frame) => frame.latencyMs),
    [20, 40, 30],
  );
  assert.equal(report.phases.initial.sourceToReceiverMs.p95, 40);
  assert.equal(report.phases.recovery.uniqueFrameIntervalMs.count, 0);
  assert.deepEqual(report.phases.recovery.dimensions, ["320x180"]);
});

test("measurement storage is bounded and reports omitted samples", () => {
  const metrics = new FrameMeasurements({ now: () => 1, limit: 2 });
  for (const sequence of [1, 2, 3]) {
    metrics.submitted(sequence, "initial");
    metrics.received(marker(sequence));
  }
  assert.equal(metrics.source.size, 2);
  assert.equal(metrics.frames.length, 2);
  assert.equal(metrics.report().droppedMeasurements, 1);
  assert.equal(distribution([]).p95, null);
});

test("external monotonic timestamps survive wrap and reject future or stale markers", () => {
  let now = 100,
    clock = 0x1000000 - 10;
  const metrics = new FrameMeasurements({ now: () => now, sourceClock: () => clock });
  metrics.received(marker(clock - 20));
  now += 40;
  clock += 40;
  metrics.received(marker((clock - 20) % 0x1000000));
  metrics.received(marker((clock - 20) % 0x1000000));
  metrics.received(marker((clock + 1) % 0x1000000));
  metrics.received(marker((clock - 60001) % 0x1000000));
  const report = metrics.report();
  assert.deepEqual(
    report.frames.map((frame) => frame.latencyMs),
    [20, 20],
  );
  assert.equal(report.outOfOrder, 0);
  assert.equal(report.duplicates, 1);
  assert.equal(report.unknownMarkers, 2);
});
