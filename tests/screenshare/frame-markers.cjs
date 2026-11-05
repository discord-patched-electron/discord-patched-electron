"use strict";

// Synthetic-source timing only. No image pixels leave the receiver callback.
// Complementary black/white rows plus a CRC reject damaged/partial markers.
const BITS = 40;
const MAGIC = 0xa7;
function checksum(sequence) {
  let crc = 0;
  for (const byte of [MAGIC, sequence >>> 16, sequence >>> 8, sequence]) {
    crc ^= byte & 255;
    for (let bit = 0; bit < 8; bit++) crc = ((crc << 1) ^ (crc & 128 ? 7 : 0)) & 255;
  }
  return crc;
}

function cells(sequence) {
  if (!Number.isInteger(sequence) || sequence < 0 || sequence > 0xffffff)
    throw new RangeError("Synthetic frame sequence must fit 24 bits");
  return [MAGIC, sequence >>> 16, sequence >>> 8, sequence, checksum(sequence)].flatMap((byte) =>
    Array.from({ length: 8 }, (_, bit) => (byte >>> (7 - bit)) & 1),
  );
}

function drawMarker(context, width, height, sequence) {
  const values = cells(sequence);
  // Relative geometry survives negotiated resolution reductions. Two rows
  // within the top 12% leave enough area for the motion/complexity pattern.
  for (let row = 0; row < 2; row++) {
    for (let bit = 0; bit < BITS; bit++) {
      context.fillStyle = values[bit] ^ row ? "white" : "black";
      const x = Math.floor((bit * width) / BITS);
      const right = Math.floor(((bit + 1) * width) / BITS);
      const y = Math.floor(row * height * 0.06);
      const bottom = Math.floor((row + 1) * height * 0.06);
      context.fillRect(x, y, right - x, bottom - y);
    }
  }
}

function readMarker(image) {
  const { width: stride, height: rows, data } = image || {};
  const {
    x: originX = 0,
    y: originY = 0,
    width = stride,
    height = rows,
  } = image?.contentRect || {};
  if (
    !Number.isInteger(width) ||
    !Number.isInteger(height) ||
    width < BITS * 2 ||
    height < 64 ||
    !data ||
    data.length < stride * rows * 4 ||
    ![originX, originY, stride, rows].every(Number.isInteger) ||
    originX < 0 ||
    originY < 0 ||
    originX + width > stride ||
    originY + height > rows
  )
    return null;
  const bytes = Array(5).fill(0);
  for (let bit = 0; bit < BITS; bit++) {
    const x = Math.floor(((bit + 0.5) * width) / BITS);
    const luminance = (row) => {
      const y = Math.floor((row + 0.5) * height * 0.06);
      const offset = ((originY + y) * stride + originX + x) * 4;
      return (data[offset] + data[offset + 1] + data[offset + 2]) / 3;
    };
    const top = luminance(0),
      bottom = luminance(1);
    // Wide thresholds tolerate range conversion and ordinary codec loss.
    if (!((top < 80 && bottom > 175) || (top > 175 && bottom < 80))) return null;
    bytes[bit >>> 3] = (bytes[bit >>> 3] << 1) | Number(top > bottom);
  }
  const sequence = (bytes[1] << 16) | (bytes[2] << 8) | bytes[3];
  return bytes[0] === MAGIC && bytes[4] === checksum(sequence) ? sequence : null;
}

function distribution(values) {
  if (!values.length) return { count: 0, min: null, p50: null, p95: null, p99: null, max: null };
  const sorted = [...values].sort((a, b) => a - b);
  const percentile = (fraction) => sorted[Math.max(0, Math.ceil(fraction * sorted.length) - 1)];
  return {
    count: sorted.length,
    min: sorted[0],
    p50: percentile(0.5),
    p95: percentile(0.95),
    p99: percentile(0.99),
    max: sorted.at(-1),
  };
}

class FrameMeasurements {
  constructor({
    now = () => performance.now(),
    limit = 20000,
    sourceClock = null,
    phase = () => "initial",
  } = {}) {
    this.now = now;
    this.limit = limit;
    this.sourceClock = sourceClock;
    this.phase = phase;
    this.source = new Map();
    this.seen = new Set();
    this.frames = [];
    this.invalidMarkers = 0;
    this.unknownMarkers = 0;
    this.duplicates = 0;
    this.outOfOrder = 0;
    this.lastSequence = -1;
    this.lastArrival = null;
    this.droppedMeasurements = 0;
    this.firstFrame = null;
  }
  submitted(sequence, phase) {
    if (this.source.size >= this.limit) this.source.delete(this.source.keys().next().value);
    this.source.set(sequence, { submittedMs: this.now(), phase });
  }
  received(image) {
    if (!this.firstFrame) {
      this.firstFrame = {
        width: image?.width,
        height: image?.height,
        dataType: image?.data?.constructor?.name,
        dataLength: image?.data?.length,
        keys: Object.keys(image || {}),
      };
    }
    const arrivedMs = this.now();
    const sequence = readMarker(image);
    if (sequence === null) {
      this.invalidMarkers++;
      return;
    }
    let source = this.source.get(sequence);
    if (!source && this.sourceClock) {
      const age = ((this.sourceClock() % 0x1000000) - sequence + 0x1000000) % 0x1000000;
      if (age <= 60000) source = { submittedMs: arrivedMs - age, phase: this.phase() };
    }
    if (!source) {
      this.unknownMarkers++;
      return;
    }
    if (this.seen.has(sequence)) {
      this.duplicates++;
      return;
    }
    if (
      this.sourceClock
        ? source.submittedMs < (this.frames.at(-1)?.submittedMs ?? -Infinity)
        : sequence < this.lastSequence
    )
      this.outOfOrder++;
    if (this.frames.length >= this.limit) {
      this.droppedMeasurements++;
      return;
    }
    this.seen.add(sequence);
    this.frames.push({
      sequence,
      phase: source.phase,
      submittedMs: source.submittedMs,
      arrivedMs,
      latencyMs: arrivedMs - source.submittedMs,
      intervalMs: this.lastArrival === null ? null : arrivedMs - this.lastArrival,
      width: image.width,
      height: image.height,
    });
    this.lastSequence = Math.max(this.lastSequence, sequence);
    this.lastArrival = arrivedMs;
  }
  report() {
    const phases = {};
    for (const phase of new Set(this.frames.map((frame) => frame.phase))) {
      const frames = this.frames.filter((frame) => frame.phase === phase);
      // Exclude cross-phase arrival intervals from a phase's cadence summary.
      const intervals = frames
        .slice(1)
        .map((frame, index) => frame.arrivedMs - frames[index].arrivedMs);
      phases[phase] = {
        uniqueFrames: frames.length,
        sourceToReceiverMs: distribution(frames.map((frame) => frame.latencyMs)),
        uniqueFrameIntervalMs: distribution(intervals),
        dimensions: [...new Set(frames.map((frame) => `${frame.width}x${frame.height}`))],
      };
    }
    return {
      scope: this.sourceClock
        ? "synthetic PipeWire generation to native receiver callback; shared CLOCK_MONOTONIC millisecond markers; phase at receipt; includes CPU output conversion, excludes physical display"
        : "synthetic canvas update to native receiver callback; same monotonic clock, includes CPU output conversion, excludes physical viewer display",
      invalidMarkers: this.invalidMarkers,
      unknownMarkers: this.unknownMarkers,
      duplicates: this.duplicates,
      outOfOrder: this.outOfOrder,
      droppedMeasurements: this.droppedMeasurements,
      firstFrame: this.firstFrame,
      phases,
      frames: this.frames,
    };
  }
}

module.exports = { cells, drawMarker, readMarker, distribution, FrameMeasurements };
