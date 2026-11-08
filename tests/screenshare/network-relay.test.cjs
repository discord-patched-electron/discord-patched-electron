const { test } = require("node:test");
const assert = require("node:assert/strict");
const { Bottleneck } = require("./network-relay.cjs");

test("directional link preserves bytes, enforces bounded tail drop, recovers and cancels queued work", () => {
  let now = 0;
  let nextId = 0;
  const pending = new Map();
  const delivered = [];
  const link = new Bottleneck({
    durationMs: 30000,
    now: () => now,
    send: (packet, port) => delivered.push({ packet, port, at: now }),
    schedule: (callback, ms) => {
      pending.set(++nextId, { callback, at: now + ms });
      return nextId;
    },
    cancel: (id) => pending.delete(id),
  });
  const advance = (target) => {
    while (pending.size) {
      const [id, item] = [...pending].sort((a, b) => a[1].at - b[1].at)[0];
      if (item.at > target) break;
      now = item.at;
      pending.delete(id);
      item.callback();
    }
    now = target;
  };
  const packet = Buffer.alloc(1000, 0xa5);
  link.start();
  for (let i = 0; i < 100; i++) assert.equal(link.enqueue(packet, 42), true);
  assert.equal(link.enqueue(packet, 42), false);
  advance(25);
  assert.equal(delivered.length, 0);
  advance(125);
  assert.equal(delivered.length, 100);
  assert.equal(delivered[0].at, 26);
  assert.equal(delivered[0].packet, packet);
  assert.equal(delivered[0].port, 42);
  advance(10000);
  for (let i = 0; i < 25; i++) assert.equal(link.enqueue(packet, 42), true);
  assert.equal(link.enqueue(packet, 42), false);
  advance(10125);
  assert.equal(delivered.length, 125);
  advance(20000);
  assert.equal(link.snapshot().capacityBps, 8000000);
  assert.equal(link.enqueue(packet, 42), true);
  assert.equal(pending.size, 1);
  link.close();
  advance(30000);
  assert.equal(delivered.length, 125);
  assert.equal(link.enqueue(packet, 42), false);
  assert.equal(pending.size, 0);
  const report = link.snapshot();
  assert.equal(report.queuedBytes, 0);
  assert.equal(report.queuedPackets, 0);
  assert.equal(report.cancelledPackets, 1);
  assert.equal(report.maximumQueueDelayMs, 100);
  assert.equal(report.phases.initial.droppedPackets, 1);
  assert.equal(report.phases.constrained.droppedPackets, 1);
});
