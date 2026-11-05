const { test } = require("node:test");
const assert = require("node:assert/strict");
const { wrap, createTracker } = require("../../integration/video/transport.cjs");

function fixture(options = {}) {
  const calls = [];
  const native = {
    setTransportOptions(value, callback) {
      assert.equal(this, native);
      calls.push(value);
      callback?.();
      return 42;
    },
    setDesktopSource(value) {
      assert.equal(this, native);
      return value;
    },
  };
  return {
    native,
    calls,
    adapter: wrap(native, { platform: "linux", measureQuality: false, ...options }),
  };
}

test("diagnostic scoring is cleared without changing codec, pacing or bitrate settings", () => {
  const { adapter, calls } = fixture();
  const encryptionSettings = Object.freeze({ mode: "fixture", secretKey: [1, 2] });
  const input = Object.freeze({
    videoQualityMeasurement: "imageQualityVmaf_v061:5000",
    videoEncoder: Object.freeze({
      name: "H264",
      type: 101,
      params: Object.freeze({
        "profile-level-id": "42e02a",
        "video-quality-measurement": "imageQualityWebrtcPsnrDb:5000",
      }),
    }),
    encodingVideoMaxBitRate: 6000000,
    captureVideoFrameRate: 60,
    encryptionSettings,
    videoEncoderExperiments: "preserve",
  });
  let callback = false;
  assert.equal(
    adapter.setTransportOptions(input, () => {
      callback = true;
    }),
    42,
  );
  assert.ok(callback);
  assert.deepEqual(calls[0], {
    ...input,
    videoQualityMeasurement: "",
    videoEncoder: {
      ...input.videoEncoder,
      params: { ...input.videoEncoder.params, "video-quality-measurement": "" },
    },
  });
  assert.equal(input.videoQualityMeasurement, "imageQualityVmaf_v061:5000");
  assert.equal(calls[0].encryptionSettings, encryptionSettings);
});

test("partial updates and native methods preserve identity, return values and errors", () => {
  const { adapter, calls, native } = fixture();
  for (const input of [{ soundsharePid: 1 }, { videoQualityMeasurement: "" }, null, [], 42]) {
    adapter.setTransportOptions(input);
    assert.equal(calls.at(-1), input);
  }
  assert.equal(adapter.setDesktopSource("fixture-screen"), "fixture-screen");
  assert.equal(wrap(adapter, { platform: "linux", measureQuality: false }), adapter);
  assert.equal(wrap(native, { platform: "linux", measureQuality: false }), adapter);
  native.setTransportOptions = () => {
    throw new Error("native failure");
  };
  assert.throws(() => adapter.setTransportOptions({}), /native failure/);
});

test("every updated scoring request is cleared; explicit diagnostic opt-in restores upstream", () => {
  const { adapter, calls } = fixture();
  for (const codec of ["H264", "AV1", "VP8"]) {
    adapter.setTransportOptions({
      videoEncoder: { name: codec },
      videoQualityMeasurement: "score",
    });
    assert.equal(calls.at(-1).videoQualityMeasurement, "");
    assert.equal(calls.at(-1).videoEncoder.name, codec);
  }
  for (const options of [{ measureQuality: true }, { platform: "win32" }]) {
    const f = fixture(options);
    assert.equal(f.adapter, f.native);
  }
});

function tracked(extra = {}) {
  const rates = [];
  const counts = { installed: true, frames: 100, pacedFrames: 10, skippedFrames: 0, pacingFps: 0 };
  let preferences = { capturePacing: true, captureMethod: "portal" };
  const tracker = createTracker({
    capture: {
      snapshot: () => ({ ...counts }),
      setRate: (fps) => {
        rates.push(fps);
        counts.pacingFps = fps;
      },
    },
    settings: { read: () => preferences },
    portal: () => true,
    ...extra,
  });
  function connection() {
    const callbacks = {};
    let polls = 0;
    const native = {
      setTransportOptions: () => 7,
      setDesktopSource: () => 8,
      setDesktopSourceWithOptions: () => 9,
      clearDesktopSource: () => 10,
      setVideoBroadcast: () => 11,
      destroy: () => 12,
      setOnDesktopSourceEnded: (callback) => {
        callbacks.ended = callback;
      },
      setOnVideoEncoderFallbackCallback: (callback) => {
        callbacks.fallback = callback;
      },
      getFilteredStats: (filter, callback) => {
        assert.equal(filter, 3, "diagnostics request only transport and outbound statistics");
        polls++;
        callback(
          JSON.stringify({
            privateKey: "never expose",
            outbound: {
              video: {
                codecName: "AV1",
                encoderImplementationName: "amd: vaapi",
                inputFrameRate: 60,
                encodeFrameRate: 58,
                framesEncoded: 200,
                mediaBitrate: 5500000,
                substreams: [
                  { isRTX: true, width: 0, height: 0 },
                  { width: 1920, height: 1080 },
                ],
              },
            },
          }),
        );
      },
    };
    return {
      native,
      callbacks,
      polls: () => polls,
      adapter: wrap(native, { platform: "linux", tracker }),
    };
  }
  return {
    rates,
    counts,
    tracker,
    connection,
    preferences: (next) => {
      preferences = next;
    },
  };
}

test("custom capture diagnostics require an active native owner and received PipeWire frames", async () => {
  let active = null;
  const rates = [];
  const f = tracked({ backends: { snapshot: () => active, setRate: (fps) => rates.push(fps) } });
  const c = f.connection();
  c.adapter.setTransportOptions({ captureVideoFrameRate: 30 });
  c.adapter.setDesktopSource("opaque", null, "screen");
  active = { method: "wayland-direct", sourceType: "screen" };
  assert.equal((await f.tracker.snapshot()).captureMethod, null);
  f.counts.frames++;
  assert.equal((await f.tracker.snapshot()).captureMethod, "wayland-direct");
  assert.equal((await f.tracker.snapshot()).sourceType, "screen");
  c.adapter.setTransportOptions({ captureVideoFrameRate: 60 });
  assert.deepEqual(rates, [30, 60]);
  active = { method: "kms", sourceType: "screen", handoff: "copy" };
  const kms = await f.tracker.snapshot();
  assert.equal(kms.captureMethod, "kms");
  assert.equal(kms.captureHandoff, "copy");
  assert.equal(kms.sourceType, "screen");
  active = null;
  assert.equal((await f.tracker.snapshot()).captureMethod, "pipewire");
  c.adapter.destroy();
});

test("negotiated AV1 without encoded frames does not imply an active hardware encoder", async () => {
  const f = tracked(),
    c = f.connection();
  c.adapter.setTransportOptions({ captureVideoFrameRate: 60, videoEncoder: { name: "AV1X" } });
  c.adapter.setDesktopSource("screen:1", null, "screen");
  let video = {
    codecName: "AV1",
    inputFrameRate: 60,
    encodeFrameRate: 0,
    framesEncoded: 0,
    mediaBitrate: 0,
  };
  c.native.getFilteredStats = (_filter, callback) =>
    callback(JSON.stringify({ outbound: { video } }));
  const pending = await f.tracker.snapshot();
  assert.equal(pending.actualCodec, "AV1");
  assert.equal(pending.encodedFrames, 0);
  assert.equal(pending.encoder, null);
  assert.equal(pending.hardware, "unknown");
  video = {
    ...video,
    encoderImplementationName: "amd: vaapi",
    framesEncoded: 60,
    encodeFrameRate: 60,
  };
  const active = await f.tracker.snapshot();
  assert.equal(active.encodedFrames, 60);
  assert.equal(active.hardware, "hardware");
  video = { codecName: "AV1" };
  assert.equal((await f.tracker.snapshot()).encodedFrames, null);
});

test("pacing follows only active shares and partial FPS updates, with stop and callback cleanup", () => {
  const f = tracked(),
    c = f.connection();
  c.adapter.setTransportOptions({ captureVideoFrameRate: 60 });
  assert.equal(f.rates.at(-1), 0);
  c.adapter.setDesktopSourceWithOptions({ sourceId: "private title", type: "window" });
  assert.equal(f.rates.at(-1), 60);
  c.adapter.setTransportOptions({ captureVideoFrameRate: 30 });
  assert.equal(f.rates.at(-1), 30);
  c.adapter.setTransportOptions({ encodingVideoMaxBitRate: 1000000 });
  assert.equal(f.rates.at(-1), 30);
  c.adapter.setVideoBroadcast(false);
  assert.equal(f.rates.at(-1), 0);
  c.adapter.setVideoBroadcast(true);
  assert.equal(f.rates.at(-1), 30);
  let ended;
  c.adapter.setOnDesktopSourceEnded((value) => {
    ended = value;
  });
  c.callbacks.ended(23);
  assert.equal(ended, 23);
  assert.equal(f.rates.at(-1), 0);
  assert.equal(c.polls(), 0);
});

test("multiple shares use highest requested rate and unknown rates disable the global limit", () => {
  const f = tracked(),
    a = f.connection().adapter,
    b = f.connection().adapter;
  a.setTransportOptions({ captureVideoFrameRate: 30 });
  a.setDesktopSource("screen:1", null, "screen");
  b.setDesktopSource("screen:2", null, "screen");
  assert.equal(f.rates.at(-1), 0);
  b.setTransportOptions({ captureVideoFrameRate: 60 });
  assert.equal(f.rates.at(-1), 60);
  b.destroy();
  assert.equal(f.rates.at(-1), 30);
  a.clearDesktopSource();
  assert.equal(f.rates.at(-1), 0);
});

test("pacing preference is read at next share; native rejection never activates a source", () => {
  const f = tracked(),
    c = f.connection();
  c.adapter.setTransportOptions({ captureVideoFrameRate: 60 });
  f.preferences({ capturePacing: false });
  c.adapter.setDesktopSource("1", null, "screen");
  assert.equal(f.rates.at(-1), 0);
  f.preferences({ capturePacing: true });
  c.adapter.setTransportOptions({ captureVideoFrameRate: 30 });
  assert.equal(f.rates.at(-1), 0);
  c.adapter.clearDesktopSource();
  c.native.setDesktopSource = () => {
    throw new Error("selection failed");
  };
  assert.throws(() => c.adapter.setDesktopSource("1"), /selection failed/);
  assert.equal(f.rates.at(-1), 0);
});

test("diagnostics report observed codec/dimensions and counters, without IDs or raw stats", async () => {
  const f = tracked(),
    c = f.connection();
  c.adapter.setTransportOptions({ captureVideoFrameRate: 60, videoEncoder: { name: "H264" } });
  c.adapter.setDesktopSourceWithOptions({ sourceId: "private title", type: "window" });
  const before = await f.tracker.snapshot();
  assert.equal(before.captureMethod, null);
  f.counts.frames += 60;
  f.counts.pacedFrames += 180;
  let callbackValue;
  c.adapter.setOnVideoEncoderFallbackCallback((value) => {
    callbackValue = value;
  });
  c.callbacks.fallback("sensitive backend error");
  assert.equal(callbackValue, "sensitive backend error");
  const status = await f.tracker.snapshot();
  assert.equal(status.captureMethod, "pipewire");
  assert.equal(status.requestedCodec, "H264");
  assert.equal(status.actualCodec, "AV1");
  assert.equal(status.hardware, "hardware");
  assert.deepEqual(status.dimensions, { width: 1920, height: 1080 });
  assert.equal(status.captureCounters.frames, 240);
  assert.equal(status.captureCounters.paced, 180);
  assert.equal(status.fallbackCount, 1);
  assert.doesNotMatch(JSON.stringify(status), /private|sensitive|secret|ssrc/i);
  c.adapter.destroy();
  assert.deepEqual(await f.tracker.snapshot(), { active: false });
});

test("a delayed stats reply cannot resurrect an ended share", async () => {
  const f = tracked(),
    c = f.connection();
  let reply;
  c.native.getFilteredStats = (_filter, callback) => {
    reply = callback;
  };
  c.adapter.setDesktopSource("1", null, "screen");
  const pending = f.tracker.snapshot();
  c.adapter.clearDesktopSource();
  reply("{}");
  assert.deepEqual(await pending, { active: false });
});

test("configured bitrate limit follows only accepted explicit video maximum updates", async () => {
  const f = tracked(),
    c = f.connection();
  c.adapter.setDesktopSource("1", null, "screen");
  assert.equal((await f.tracker.snapshot()).encoding.configuredMaxBitrate, null);
  assert.equal(c.adapter.setTransportOptions({ encodingVideoMaxBitRate: 6000000 }), 7);
  c.adapter.setTransportOptions({
    encodingVideoBitRate: 4000000,
    callMaxBitRate: 12000000,
    streamParameters: [{ maxBitrate: 10000000 }],
  });
  assert.equal((await f.tracker.snapshot()).encoding.configuredMaxBitrate, 6000000);
  const accepted = c.native.setTransportOptions;
  c.native.setTransportOptions = () => {
    throw new Error("native options rejected");
  };
  assert.throws(
    () => c.adapter.setTransportOptions({ encodingVideoMaxBitRate: 1000000 }),
    /native options rejected/,
  );
  assert.equal((await f.tracker.snapshot()).encoding.configuredMaxBitrate, 6000000);
  c.native.setTransportOptions = accepted;
  for (const maximum of [0, -1, NaN, Infinity, "6000000", null]) {
    c.adapter.setTransportOptions({ encodingVideoMaxBitRate: maximum });
    assert.equal((await f.tracker.snapshot()).encoding.configuredMaxBitrate, null);
  }
  c.adapter.destroy();
});

test("encoding diagnostics whitelist connection estimate and reported stream totals without summing", async () => {
  const f = tracked(),
    c = f.connection();
  const video = {
    mediaBitrate: 5000000,
    targetMediaBitrate: 9000000,
    framesDroppedRateLimiter: 42,
    framesDroppedEncoderQueue: 0,
    framesDroppedCongestionWindow: 7,
    bwLimitedResolution: true,
    bwLimitedFrameRate: false,
    numberOfQualityAdaptChanges: 1,
    substreams: [
      { width: 1920, height: 1080, framesDroppedRateLimiter: 42, ssrc: 123 },
      { width: 1280, height: 720, framesDroppedRateLimiter: 42, ssrc: 124 },
      { isRTX: true, framesDroppedRateLimiter: 42, ssrc: 125 },
    ],
    privateKey: "secret-video-key",
  };
  let report = {
    transport: { sendBandwidth: 10000000, privateAddress: "secret-address" },
    outbound: { video },
    inbound: [{ userId: "secret-user-id", video: { mediaBitrate: 1000000 } }],
  };
  const filters = [];
  c.native.getFilteredStats = (filter, callback) => {
    filters.push(filter);
    callback(JSON.stringify(report));
  };
  c.adapter.setTransportOptions({ encodingVideoMaxBitRate: 6000000 });
  c.adapter.setDesktopSource("secret-source-id", null, "screen");
  const status = await f.tracker.snapshot();
  assert.deepEqual(filters, [3]);
  assert.deepEqual(status.encoding, {
    configuredMaxBitrate: 6000000,
    estimatedConnectionBandwidth: 10000000,
    averageQp: null,
    rateLimiterDiscards: 42,
    encoderQueueDiscards: 0,
    congestionWindowDiscards: 7,
    keyFramesEncoded: null,
  });
  assert.equal(status.bitrate, 5000000);
  assert.equal(status.adaptation.bwLimitedResolution, true);
  assert.equal(status.adaptation.qualityChanges, 1);
  assert.doesNotMatch(JSON.stringify(status), /secret|private|ssrc|targetMediaBitrate|9000000/);

  // Partial native stats never inherit a previous estimate or invent a counter
  // from substreams, targetMediaBitrate, or another discard cause.
  report = { outbound: { video: { targetMediaBitrate: 9000000, framesDroppedEncoderQueue: 0 } } };
  const partial = await f.tracker.snapshot();
  assert.equal(partial.bitrate, null);
  assert.deepEqual(partial.encoding, {
    configuredMaxBitrate: 6000000,
    estimatedConnectionBandwidth: null,
    averageQp: null,
    rateLimiterDiscards: null,
    encoderQueueDiscards: 0,
    congestionWindowDiscards: null,
    keyFramesEncoded: null,
  });
  for (const invalid of [-1, 1.5, Number.MAX_SAFE_INTEGER + 1, "42", null]) {
    report = {
      transport: { sendBandwidth: "10000000" },
      outbound: {
        video: {
          framesDroppedRateLimiter: invalid,
          framesDroppedEncoderQueue: invalid,
          framesDroppedCongestionWindow: invalid,
        },
      },
    };
    const unknown = (await f.tracker.snapshot()).encoding;
    assert.equal(unknown.estimatedConnectionBandwidth, null);
    for (const key of ["rateLimiterDiscards", "encoderQueueDiscards", "congestionWindowDiscards"])
      assert.equal(unknown[key], null);
  }
  c.native.getFilteredStats = () => {
    throw new Error("secret native statistics failure");
  };
  const unavailable = await f.tracker.snapshot();
  assert.equal(unavailable.active, true);
  assert.equal(unavailable.encoding.configuredMaxBitrate, 6000000);
  assert.equal(unavailable.encoding.estimatedConnectionBandwidth, null);
  assert.equal(unavailable.encoding.rateLimiterDiscards, null);
  assert.doesNotMatch(JSON.stringify(unavailable), /secret/);
  c.adapter.destroy();
});

test("quantizer diagnostics use recent frames and reset across codec, size and counter changes", async () => {
  const f = tracked(),
    c = f.connection();
  let video = {
    codecName: "H264",
    encoderImplementationName: "amd: vaapi",
    framesEncoded: 100,
    qpSum: 2000,
    substreams: [{ width: 1920, height: 1080, frameCounts: { keyFrames: 1 } }],
  };
  c.native.getFilteredStats = (_, callback) => callback(JSON.stringify({ outbound: { video } }));
  c.adapter.setDesktopSource("1", null, "screen");
  assert.equal((await f.tracker.snapshot()).encoding.averageQp, null);
  video = { ...video, framesEncoded: 160, qpSum: 4520 };
  let encoding = (await f.tracker.snapshot()).encoding;
  assert.equal(encoding.averageQp, 42);
  assert.equal(encoding.keyFramesEncoded, 1);
  assert.equal((await f.tracker.snapshot()).encoding.averageQp, null);
  video = {
    ...video,
    framesEncoded: 220,
    qpSum: 6620,
    substreams: [{ width: 1486, height: 836, frameCounts: { keyFrames: 2 } }],
  };
  assert.equal((await f.tracker.snapshot()).encoding.averageQp, null);
  video = { ...video, framesEncoded: 280, qpSum: 8420 };
  assert.equal((await f.tracker.snapshot()).encoding.averageQp, 30);
  video = { ...video, codecName: "AV1", framesEncoded: 340, qpSum: 20420 };
  assert.equal((await f.tracker.snapshot()).encoding.averageQp, null);
  video = { ...video, framesEncoded: 400, qpSum: 32420 };
  assert.equal((await f.tracker.snapshot()).encoding.averageQp, 200);
  video = { ...video, framesEncoded: 10, qpSum: 1800 };
  assert.equal((await f.tracker.snapshot()).encoding.averageQp, null);
  video = { ...video, framesEncoded: 20, qpSum: 5000 };
  assert.equal((await f.tracker.snapshot()).encoding.averageQp, null);
  delete video.qpSum;
  assert.equal((await f.tracker.snapshot()).encoding.averageQp, null);
  video = { ...video, framesEncoded: 30, qpSum: 6000 };
  assert.equal((await f.tracker.snapshot()).encoding.averageQp, null);
  c.adapter.setDesktopSource("2", null, "screen");
  video = { ...video, framesEncoded: 40, qpSum: 8000 };
  assert.equal((await f.tracker.snapshot()).encoding.averageQp, null);
  c.adapter.destroy();
});

test("late statistics cannot overwrite the latest quantizer baseline", async () => {
  const f = tracked(),
    c = f.connection(),
    replies = [];
  c.native.getFilteredStats = (_, callback) => replies.push(callback);
  c.adapter.setDesktopSource("1", null, "screen");
  const sample = (frames, sum) =>
    JSON.stringify({
      outbound: { video: { codecName: "H264", framesEncoded: frames, qpSum: sum } },
    });
  const first = f.tracker.snapshot();
  const second = f.tracker.snapshot();
  replies[1](sample(200, 6000));
  assert.equal((await second).encoding.averageQp, null);
  replies[0](sample(100, 2000));
  assert.equal((await first).encoding.averageQp, null);
  const third = f.tracker.snapshot();
  replies[2](sample(260, 8520));
  assert.equal((await third).encoding.averageQp, 42);
  c.adapter.destroy();
});

test("producer diagnostics distinguish a negotiated ceiling from the requested cap", async () => {
  const f = tracked(),
    c = f.connection();
  c.adapter.setTransportOptions({ captureVideoFrameRate: 60 });
  c.adapter.setDesktopSource("1", null, "screen");
  f.counts.frames++;
  f.counts.upstream = {
    installed: true,
    activeStreams: 1,
    appliedFps: 60,
    negotiatedMaxFpsNumerator: 30000,
    negotiatedMaxFpsDenominator: 1001,
  };
  const status = await f.tracker.snapshot();
  assert.equal(status.captureCounters.upstreamAppliedFps, 60);
  assert.equal(status.captureCounters.upstreamFps, 30000 / 1001);
  for (const override of [
    { installed: false },
    { activeStreams: 0 },
    { activeStreams: 2 },
    { negotiatedMaxFpsDenominator: 0 },
  ]) {
    const original = f.counts.upstream;
    f.counts.upstream = { ...original, ...override };
    assert.equal((await f.tracker.snapshot()).captureCounters.upstreamFps, null);
    f.counts.upstream = original;
  }
  f.counts.upstream.appliedFps = 0;
  const observed = (await f.tracker.snapshot()).captureCounters;
  assert.equal(observed.upstreamAppliedFps, null);
  assert.equal(observed.upstreamFps, 30000 / 1001);
  c.adapter.destroy();
});

test("process counters are not attributed to one share during concurrent sharing", async () => {
  const f = tracked(),
    a = f.connection().adapter,
    b = f.connection().adapter;
  a.setDesktopSource("1", null, "screen");
  b.setDesktopSource("2", null, "screen");
  f.counts.frames += 100;
  const status = await f.tracker.snapshot();
  assert.equal(status.captureCounters, null);
  assert.equal(status.captureMethod, null);
  assert.equal(status.sourceType, null); // Portal routing does not identify source type.
  b.clearDesktopSource();
  f.counts.frames += 10;
  assert.equal((await f.tracker.snapshot()).captureCounters, null);
  a.setDesktopSource("3", null, "screen");
  f.counts.frames += 10;
  assert.equal((await f.tracker.snapshot()).captureCounters.frames, 10);
  a.setDesktopSource("", false, "");
  assert.deepEqual(await f.tracker.snapshot(), { active: false });
});

test("custom source loss clears only its connection and notifies Discord once", async () => {
  let lifecycle;
  const f = tracked({ backends: { subscribe: (callback) => (lifecycle = callback) } });
  const first = f.connection();
  let ended = 0,
    cleared = 0;
  first.native.clearDesktopSource = () => {
    cleared++;
    first.callbacks.ended();
  };
  first.adapter.setOnDesktopSourceEnded(() => ended++);
  first.adapter.setDesktopSource("1", false, "screen");
  lifecycle({ event: "started", requestId: 41 });
  lifecycle({ event: "ended", requestId: 40 });
  assert.equal(cleared, 0);
  lifecycle({ event: "ended", requestId: 41 });
  lifecycle({ event: "ended", requestId: 41 });
  first.callbacks.ended();
  assert.equal(cleared, 1);
  assert.equal(ended, 1);
  assert.equal((await f.tracker.snapshot()).active, false);
  first.adapter.setDesktopSource("2", false, "window");
  lifecycle({ event: "started", requestId: 42 });
  lifecycle({ event: "ended", requestId: 41 });
  assert.equal(cleared, 1, "old request cannot end the replacement source");
  first.callbacks.ended();
  assert.equal(ended, 2, "ordinary native end still reaches Discord for the new source");
});

test("ambiguous custom source start never binds an unrelated connection", () => {
  let lifecycle;
  const f = tracked({ backends: { subscribe: (callback) => (lifecycle = callback) } });
  let ended = 0;
  for (const c of [f.connection(), f.connection()]) {
    c.adapter.setOnDesktopSourceEnded(() => ended++);
    c.adapter.setDesktopSource("1", false, "screen");
  }
  lifecycle({ event: "started", requestId: 1 });
  lifecycle({ event: "ended", requestId: 1 });
  assert.equal(ended, 0);
});

test("H264 receive options use the proven preloaded library instead of Discord's disposable cache", () => {
  const library = "/profile with spaces/dpe-codecs/openh264/libopenh264-2.5.1-linux64.7.so";
  const decoder = Object.freeze({
    name: "H264",
    type: 101,
    params: Object.freeze({
      libopenh264: "1",
      "libopenh264-path": "/profile/discord_asset_cache/openh264/old.so",
      "hardware-h264": "0",
      "profile-level-id": "42e034",
    }),
  });
  const av1 = Object.freeze({ name: "AV1", type: 105, params: { libopenh264: "1" } });
  const input = Object.freeze({
    videoDecoders: Object.freeze([decoder, av1]),
    encodingVideoMaxBitRate: 16000000,
  });
  const { adapter, calls } = fixture({ openh264Library: library, measureQuality: true });
  adapter.setTransportOptions(input);
  assert.equal(calls[0].videoDecoders[0].params["libopenh264-path"], library);
  assert.equal(calls[0].videoDecoders[0].params["hardware-h264"], "0");
  assert.equal(calls[0].videoDecoders[1], av1);
  assert.equal(calls[0].encodingVideoMaxBitRate, 16000000);
  assert.equal(decoder.params["libopenh264-path"], "/profile/discord_asset_cache/openh264/old.so");
  for (const params of [
    { libopenh264: "0" },
    { libopenh264: "1", "libopenh264-path": "/custom/decoder.so" },
    { "libopenh264-path": "/profile/discord_asset_cache/openh264/old.so" },
  ]) {
    const options = { videoDecoders: [{ name: "H264", params }] };
    adapter.setTransportOptions(options);
    assert.equal(calls.at(-1), options);
  }
  for (const options of [{ videoDecoders: [] }, { videoDecoders: null }, { audioBitrate: 10000 }]) {
    adapter.setTransportOptions(options);
    assert.equal(calls.at(-1), options);
  }
  const unproven = fixture({ openh264Library: null });
  unproven.adapter.setTransportOptions(input);
  assert.equal(unproven.calls[0], input);
});

test("OpenH264 preload discovery accepts mapped managed paths and fails closed on ambiguity", () => {
  const { preloadedOpenH264 } = require("../../integration/video/transport.cjs");
  const library = "/profile with spaces/dpe-codecs/openh264/libopenh264-2.5.1-linux64.7.so";
  const line = (path, offset = "00000000") =>
    `12340000-12400000 r-xp ${offset} 08:01 123 ${path}\n`;
  const encoded = library.replaceAll(" ", "\\040");
  assert.equal(
    preloadedOpenH264({ env: {}, readMaps: () => line(encoded) + line(encoded, "00002000") }),
    library,
  );
  const custom = "/chosen/libopenh264.so";
  assert.equal(
    preloadedOpenH264({ env: { DPE_OPENH264_LIBRARY: custom }, readMaps: () => line(custom) }),
    custom,
  );
  for (const maps of [
    line(custom),
    line(encoded + " (deleted)"),
    line(encoded) + line(custom),
    "",
    "x".repeat(4 * 1024 * 1024 + 1),
  ])
    assert.equal(preloadedOpenH264({ env: {}, readMaps: () => maps }), null);
  assert.equal(
    preloadedOpenH264({
      env: {},
      readMaps: () => {
        throw new Error("sandbox");
      },
    }),
    null,
  );
});

test("incoming decoder diagnostics use active native reports and omit identities", async () => {
  let inbound = [
    {
      userId: "private-user",
      video: {
        codecName: "AV1X",
        decoderImplementationName: "electron",
        framesDecoded: 12,
        width: 1920,
        height: 1072,
        decodeFrameRate: 60,
        ssrc: 123,
        privateKey: "secret",
      },
    },
  ];
  const tracker = createTracker({
    encoder: { decoderHardware: (name) => (name === "electron" ? true : null) },
  });
  const native = {
    setTransportOptions() {},
    destroy() {},
    getFilteredStats(filter, callback) {
      assert.equal(filter, 4);
      callback(JSON.stringify({ inbound }));
    },
  };
  const adapter = wrap(native, { tracker, platform: "linux" });
  const result = await tracker.snapshot();
  assert.deepEqual(result.watching, [
    {
      codec: "AV1X",
      decoder: "electron",
      hardware: "hardware",
      width: 1920,
      height: 1072,
      fps: 60,
    },
  ]);
  assert.equal(JSON.stringify(result).includes("private"), false);
  inbound[0].video.decoderImplementationName = "future-decoder";
  assert.equal((await tracker.snapshot()).watching[0].hardware, "unknown");
  inbound[0].video.framesDecoded = 0;
  assert.equal((await tracker.snapshot()).watching, undefined);
  adapter.destroy();
  assert.deepEqual(await tracker.snapshot(), { active: false });
});

test("source geometry follows stable PipeWire resize and clears when sharing stops", (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const previousWindow = global.window,
    previousEvent = global.CustomEvent;
  const sizes = [];
  global.window = { dispatchEvent: (event) => sizes.push(JSON.parse(event.detail)) };
  global.CustomEvent = class {
    constructor(type, options) {
      this.type = type;
      this.detail = options.detail;
    }
  };
  t.after(() => {
    global.window = previousWindow;
    global.CustomEvent = previousEvent;
  });
  const f = tracked();
  f.counts.upstream = { activeStreams: 1, width: 1920, height: 1080 };
  const c = f.connection();
  c.adapter.setDesktopSource("private", null, "screen");
  t.mock.timers.tick(1000);
  assert.deepEqual(sizes.at(-1), { width: 1920, height: 1080 });
  f.counts.upstream.width = 1280;
  f.counts.upstream.height = 720;
  t.mock.timers.tick(1000);
  assert.deepEqual(sizes.at(-1), { width: 1280, height: 720 });
  c.adapter.clearDesktopSource();
  assert.deepEqual(sizes.at(-1), { width: 0, height: 0 });
});
