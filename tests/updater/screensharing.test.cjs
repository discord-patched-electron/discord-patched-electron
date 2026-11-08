const assert = require("node:assert/strict");
const { test } = require("node:test");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const nativeSettings = require("../../integration/video/settings.cjs");

function fixture() {
  const ipcMain = new EventEmitter();
  const requests = [];
  const sender = {
    mainFrame: {},
    send: (...args) => requests.push(args),
    executeJavaScript: async () => {},
  };
  const electron = { ipcMain, BrowserWindow: { getAllWindows: () => [{ webContents: sender }] } };
  const context = {
    module: { exports: {} },
    require(name) {
      if (name === "electron") return electron;
      if (name === "./presentation.cjs") return { trusted: (contents) => contents === sender };
      if (name === "../video/settings.cjs") return nativeSettings;
      if (name.startsWith("../")) return require(path.resolve("integration/updater", name));
      return require(name);
    },
    global: {},
    process,
    setTimeout,
    clearTimeout,
  };
  vm.runInNewContext(fs.readFileSync("integration/updater/screensharing.cjs", "utf8"), context);
  const reply = (
    value,
    event = { sender, senderFrame: sender.mainFrame },
    id = requests.at(-1)?.[1],
  ) => ipcMain.emit("dpe-screenshare-status-response", event, id, value);
  return { api: context.module.exports, ipcMain, requests, sender, electron, reply };
}

test("screenshare preferences default safely and reject unavailable methods", async () => {
  assert.deepEqual(nativeSettings.normalize({ captureMethod: "invalid", capturePacing: "no" }), {
    captureMethod: "auto",
    capturePacing: true,
    experimentalCapture: false,
    preserveViewerBitrate: false,
    excludedCodecs: [],
  });
  const f = fixture();
  let state = { css: "keep", desktop: { webcamFlip: true } };
  let fail = false;
  const store = {
    get: () => state,
    set: (_key, value) => {
      state = value;
    },
    save: () => {
      if (fail) throw new Error("disk full");
    },
  };
  await f.api.save(store, { capturePacing: false });
  assert.equal(state.screensharing.capturePacing, false);
  assert.equal(state.css, "keep");
  assert.equal(state.desktop.webcamFlip, true);
  await f.api.save(store, { preserveViewerBitrate: true });
  assert.equal(state.screensharing.preserveViewerBitrate, true);
  await f.api.save(store, { preserveViewerBitrate: false });
  assert.equal(state.screensharing.preserveViewerBitrate, false);
  const previous = state;
  fail = true;
  await assert.rejects(f.api.save(store, { capturePacing: true }), /disk full/);
  assert.equal(state, previous);
  fail = false;
  for (const patch of [
    { captureMethod: "invalid" },
    { capturePacing: 1 },
    { preserveViewerBitrate: "true" },
    { experimentalCapture: "true" },
    { excludedCodecs: "AV1" },
    { excludedCodecs: ["unknown"] },
    { excludedCodecs: ["VP9"] },
    { excludedCodecs: ["AV1", "H265", "H264", "VP8"] },
    { excludedCodecs: ["AV1", "H265", "H264", "VP9", "VP8"] },
    { foo: true },
  ])
    await assert.rejects(f.api.save(store, patch), /supported screensharing/);
  const unavailable = f.api.save(store, { captureMethod: "portal" });
  f.reply({ active: false, methods: ["auto"] });
  await assert.rejects(unavailable, /unavailable in the current session/);
  const available = f.api.save(store, { captureMethod: "portal" });
  f.reply({ active: false, methods: ["auto", "portal"] });
  await available;
  assert.equal(state.screensharing.captureMethod, "portal");
  await f.api.save(store, { experimentalCapture: true });
  assert.equal(state.screensharing.experimentalCapture, true);
  assert.equal(state.screensharing.capturePacing, false);
  await f.api.save(store, { experimentalCapture: false });
  assert.equal(state.screensharing.experimentalCapture, false);
  assert.equal(state.screensharing.capturePacing, false);
  await f.api.save(store, { excludedCodecs: ["VP8", "AV1", "AV1"] });
  assert.deepEqual(state.screensharing.excludedCodecs, ["AV1", "VP8"]);
  assert.equal(state.screensharing.capturePacing, false);
});

test("codec preferences are bounded and do not permit an empty allowed set", () => {
  assert.deepEqual(nativeSettings.CODECS, ["AV1", "H265", "H264", "VP8"]);
  assert.deepEqual(nativeSettings.normalize({ excludedCodecs: ["VP9", "H264"] }).excludedCodecs, [
    "H264",
  ]);
  // Old profiles could leave only the nonexistent VP9 encoder allowed.
  assert.deepEqual(
    nativeSettings.normalize({ excludedCodecs: ["AV1", "H265", "H264", "VP8"] }).excludedCodecs,
    [],
  );
  assert.deepEqual(nativeSettings.normalize({ excludedCodecs: "AV1" }).excludedCodecs, []);
  assert.deepEqual(
    nativeSettings.normalize({ excludedCodecs: ["AV1X", "AV1", "private"] }).excludedCodecs,
    ["AV1"],
  );
  assert.deepEqual(
    nativeSettings.normalize({ excludedCodecs: nativeSettings.CODECS }).excludedCodecs,
    [],
  );
  const { api } = fixture();
  assert.equal(
    api.sanitize({ codecExclusionError: "no-allowed-encoder" }).codecExclusionError,
    "no-allowed-encoder",
  );
  assert.equal(api.sanitize({ codecExclusionError: "private details" }).codecExclusionError, null);
});

test("experimental capture requires explicit opt-in and never overrides Vanilla's method", () => {
  const { api } = fixture();
  for (const captureMethod of ["auto", "portal", "x11", "kms", "wayland-direct", "vkcapture"]) {
    assert.equal(nativeSettings.normalize({ captureMethod }).experimentalCapture, false);
    for (const experimentalCapture of [false, true]) {
      const policy = api.policy(
        { captureMethod, experimentalCapture },
        { WAYLAND_DISPLAY: "wayland-1" },
        "linux",
      );
      assert.equal(policy.method, "auto");
      assert.equal(policy.experimentalCapture, experimentalCapture);
    }
  }
});

test("live screenshare diagnostics require exact trusted main-frame replies and omit private data", async () => {
  const f = fixture();
  const first = f.api.snapshot();
  assert.equal(f.api.snapshot(), first, "simultaneous settings polls share one native request");
  assert.equal(f.requests.length, 1);
  f.reply({ active: true }, { sender: {}, senderFrame: {} });
  f.reply({ active: true }, { sender: f.sender, senderFrame: {} });
  f.reply({ active: true }, undefined, "wrong request");
  assert.equal(f.ipcMain.listenerCount("dpe-screenshare-status-response"), 1);
  f.reply({
    active: true,
    captureMethod: "portal",
    captureHandoff: "copy",
    captureHandoffFallback: "synchronization-unavailable",
    actualCodec: "AV1",
    encoder: "VaapiVideoEncoder",
    methods: ["portal", "vkcapture"],
    methodReasons: { kms: "permission-required", private: "private" },
    dimensions: { width: 1920, height: 1080 },
    outputFps: 60,
    userId: "private",
    sourceId: "window:123",
    session: { token: "private" },
    captureCounters: {
      frames: 20,
      paced: 10,
      upstreamFps: 60000 / 1001,
      upstreamAppliedFps: 60,
      private: "private",
    },
    adaptation: {
      cpuLimitedFrameRate: true,
      cpuLimitedResolution: "false",
      bwLimitedFrameRate: false,
      bwLimitedResolution: null,
      cpuChanges: -1,
      qualityChanges: 1,
      private: "private",
    },
    encoding: {
      configuredMaxBitrate: 6000000,
      estimatedConnectionBandwidth: 10000000,
      averageQp: 38.25,
      keyFramesEncoded: 3,
      rateLimiterDiscards: 42,
      encoderQueueDiscards: 0,
      congestionWindowDiscards: 3,
      private: "private",
    },
  });
  const result = await first;
  assert.equal(result.state, "active");
  assert.equal(result.captureHandoff, "copy");
  assert.equal(result.captureHandoffFallback, "synchronization-unavailable");
  assert.equal(
    f.api.sanitize({ captureHandoffFallback: "upstream-implicit-sync" }).captureHandoffFallback,
    "upstream-implicit-sync",
  );
  assert.equal(result.actualCodec, "AV1");
  assert.equal(
    result.requestedCodec,
    null,
    "requested codec is never fabricated from actual codec",
  );
  assert.equal(result.hardware, "unknown", "hardware status needs positive runtime evidence");
  assert.equal(result.dimensions.width, 1920);
  assert.equal(result.captureCounters.upstreamFps, 60000 / 1001);
  assert.equal(result.captureCounters.upstreamAppliedFps, 60);
  assert.equal(result.adaptation.cpuLimitedFrameRate, true);
  assert.equal(result.adaptation.cpuLimitedResolution, null);
  assert.equal(result.adaptation.bwLimitedFrameRate, false);
  assert.equal(result.adaptation.bwLimitedResolution, null);
  assert.equal(result.adaptation.cpuChanges, null);
  assert.equal(
    result.adaptation.qualityChanges,
    1,
    "quality changes do not imply bandwidth limits",
  );
  assert.deepEqual(Array.from(result.methods), ["auto", "portal", "vkcapture"]);
  assert.equal(result.methodReasons.kms, "permission-required");
  assert.equal(result.encoding.configuredMaxBitrate, 6000000);
  assert.equal(result.encoding.estimatedConnectionBandwidth, 10000000);
  assert.equal(result.encoding.averageQp, 38.25);
  assert.equal(result.encoding.keyFramesEncoded, 3);
  assert.equal(
    f.api.sanitize({ captureHandoffFallback: "cursor-composition" }).captureHandoffFallback,
    "cursor-composition",
  );
  assert.equal(result.encoding.rateLimiterDiscards, 42);
  assert.equal(result.encoding.encoderQueueDiscards, 0);
  assert.equal(result.encoding.congestionWindowDiscards, 3);
  assert.equal(JSON.stringify(result).includes("private"), false);
  assert.equal(f.ipcMain.listenerCount("dpe-screenshare-status-response"), 0);
});

test("KMS reasons are fixed codes and disappear when the method becomes available", () => {
  const { api } = fixture();
  for (const reason of ["permission-blocked", "authorization-unavailable"])
    assert.equal(api.sanitize({ methodReasons: { kms: reason } }).methodReasons.kms, reason);
  assert.equal(
    api.sanitize({ methodReasons: { kms: "/private/device" } }).methodReasons.kms,
    undefined,
  );
  assert.equal(
    api.sanitize({ methods: ["kms"], methodReasons: { kms: "helper-missing" } }).methodReasons.kms,
    undefined,
  );
  assert.equal(
    api.sanitize({ methodReasons: { kms: "native-picker-unavailable" } }).methodReasons.kms,
    "native-picker-unavailable",
  );
  assert.equal(nativeSettings.normalize({ captureMethod: "kms" }).captureMethod, "kms");
});

test("encoding diagnostics retain unknown values and reject invalid totals", () => {
  const { api } = fixture();
  assert.equal(api.sanitize({ encodedFrames: 0 }).encodedFrames, 0);
  for (const invalid of [undefined, null, -1, 1.5, Number.MAX_SAFE_INTEGER + 1, "0"])
    assert.equal(api.sanitize({ encodedFrames: invalid }).encodedFrames, null);
  assert.equal(api.sanitize({ encoding: [] }).encoding, null);
  assert.equal(api.sanitize({}).encoding, null);
  for (const invalid of [undefined, null, 0, -1, Infinity, NaN, "6000000"])
    assert.equal(
      api.sanitize({ encoding: { configuredMaxBitrate: invalid } }).encoding.configuredMaxBitrate,
      null,
    );
  for (const invalid of [undefined, null, -1, 1.5, Number.MAX_SAFE_INTEGER + 1, "42"])
    for (const name of [
      "rateLimiterDiscards",
      "encoderQueueDiscards",
      "congestionWindowDiscards",
      "keyFramesEncoded",
    ])
      assert.equal(api.sanitize({ encoding: { [name]: invalid } }).encoding[name], null);
  for (const value of [null, -1, 256, NaN, Infinity, "42"])
    assert.equal(api.sanitize({ encoding: { averageQp: value } }).encoding.averageQp, null);
  for (const value of [0, 38.25, 255])
    assert.equal(api.sanitize({ encoding: { averageQp: value } }).encoding.averageQp, value);
  const status = api.sanitize({ encoding: { configuredMaxBitrate: 6000000 } });
  assert.equal(status.encoding.estimatedConnectionBandwidth, null);
  assert.equal(status.encoding.rateLimiterDiscards, null);
});

test("producer limit remains unknown without a positive negotiated rate", () => {
  const { api } = fixture();
  for (const upstreamFps of [undefined, null, 0, -1, Infinity, "60"]) {
    const status = api.sanitize({ captureCounters: { upstreamFps, upstreamAppliedFps: 60 } });
    assert.equal(status.captureCounters.upstreamFps, null);
    assert.equal(status.captureCounters.upstreamAppliedFps, 60);
  }
  assert.equal(
    api.sanitize({ captureCounters: { upstreamAppliedFps: 0 } }).captureCounters.upstreamAppliedFps,
    null,
  );
});

test("missing diagnostics time out without claiming the share is inactive", async () => {
  const f = fixture();
  const result = await f.api.snapshot({ timeout: 5 });
  assert.equal(result.state, "unavailable");
  assert.equal(result.active, undefined);
  assert.equal(f.ipcMain.listenerCount("dpe-screenshare-status-response"), 0);
});

test("native settings bridge publishes current snapshots only for bounded request IDs", async () => {
  const ipc = new EventEmitter();
  const responses = [];
  ipc.send = (...args) => responses.push(args);
  nativeSettings.install(
    { snapshot: async () => ({ active: false }), methods: () => ["auto", "portal"] },
    ipc,
  );
  ipc.emit("dpe-screenshare-status-request", {}, "unbounded input");
  assert.equal(responses.length, 0);
  const id = "12345678-1234-1234-1234-123456789012";
  ipc.emit("dpe-screenshare-status-request", {}, id);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(responses, [
    [
      "dpe-screenshare-status-response",
      id,
      { active: false, codecExclusionError: null, methods: ["auto", "portal"] },
    ],
  ]);
});
