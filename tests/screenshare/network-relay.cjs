"use strict";

// Synthetic sender -> receiver link only. Reverse feedback stays unconstrained.
// No packet parsing, payload changes, host traffic shaping or unbounded buffers.
class Bottleneck {
  constructor({ send, durationMs, now, schedule, cancel }) {
    this.send = send;
    this.durationMs = durationMs;
    this.phaseDurationMs = Math.min(10000, durationMs / 3);
    this.now = now;
    this.schedule = schedule;
    this.cancel = cancel;
    this.started = null;
    this.closed = false;
    this.nextDeparture = 0;
    this.queue = [];
    this.bytes = 0;
    this.timer = null;
    this.maximumQueuedBytes = 0;
    this.maximumQueueDelayMs = 0;
    this.maximumDeliveryLatenessMs = 0;
    this.cancelledPackets = 0;
    this.phases = Object.fromEntries(
      ["initial", "constrained", "recovery"].map((phase) => [
        phase,
        {
          acceptedPackets: 0,
          acceptedBytes: 0,
          droppedPackets: 0,
          droppedBytes: 0,
          deliveredPackets: 0,
          deliveredBytes: 0,
        },
      ]),
    );
  }

  start() {
    if (this.started === null) this.started = this.now();
  }

  phase() {
    const elapsed = this.started === null ? 0 : this.now() - this.started;
    return elapsed < this.phaseDurationMs
      ? "initial"
      : elapsed < this.phaseDurationMs * 2
        ? "constrained"
        : "recovery";
  }

  enqueue(packet, port) {
    if (this.closed) return false;
    if (this.started === null) {
      this.send(packet, port);
      return true;
    }
    const now = this.now();
    const phase = this.phase();
    const counts = this.phases[phase];
    const bitrate = phase === "constrained" ? 2000000 : 8000000;
    const departure = Math.max(now, this.nextDeparture) + (packet.length * 8000) / bitrate;
    const delay = departure - now;
    // Tail drop after 100 ms serialization backlog. 25 ms propagation is
    // separate. Previously admitted packets keep their <=100 ms reservation
    // across a phase boundary. Absolute bounds also guard scheduler stalls.
    if (delay > 100 || this.bytes + packet.length > 256 * 1024 || this.queue.length >= 2048) {
      counts.droppedPackets++;
      counts.droppedBytes += packet.length;
      return false;
    }
    this.nextDeparture = departure;
    this.queue.push({ packet, port, due: departure + 25 });
    this.bytes += packet.length;
    counts.acceptedPackets++;
    counts.acceptedBytes += packet.length;
    this.maximumQueuedBytes = Math.max(this.maximumQueuedBytes, this.bytes);
    this.maximumQueueDelayMs = Math.max(this.maximumQueueDelayMs, delay);
    this.arm();
    return true;
  }

  arm() {
    if (this.timer !== null || !this.queue.length) return;
    this.timer = this.schedule(
      () => {
        this.timer = null;
        const now = this.now();
        while (this.queue.length && this.queue[0].due <= now) {
          const { packet, port, due } = this.queue.shift();
          this.maximumDeliveryLatenessMs = Math.max(this.maximumDeliveryLatenessMs, now - due);
          this.bytes -= packet.length;
          const counts = this.phases[this.phase()];
          counts.deliveredPackets++;
          counts.deliveredBytes += packet.length;
          this.send(packet, port);
        }
        this.arm();
      },
      Math.max(1, Math.ceil(this.queue[0].due - this.now())),
    );
  }

  close() {
    this.closed = true;
    if (this.timer !== null) this.cancel(this.timer);
    this.timer = null;
    this.cancelledPackets += this.queue.length;
    this.queue = [];
    this.bytes = 0;
  }

  snapshot() {
    return {
      phase: this.phase(),
      capacityBps: this.phase() === "constrained" ? 2000000 : 8000000,
      phaseDurationMs: this.phaseDurationMs,
      propagationMs: 25,
      queueLimitMs: 100,
      phases: structuredClone(this.phases),
      queuedPackets: this.queue.length,
      queuedBytes: this.bytes,
      maximumQueuedBytes: this.maximumQueuedBytes,
      maximumQueueDelayMs: this.maximumQueueDelayMs, // Nominal serialization; lateness is separate.
      maximumDeliveryLatenessMs: this.maximumDeliveryLatenessMs,
      cancelledPackets: this.cancelledPackets,
      closed: this.closed,
    };
  }
}

module.exports = { Bottleneck };
