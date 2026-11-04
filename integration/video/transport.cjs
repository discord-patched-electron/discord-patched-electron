"use strict";

// Optional VMAF/PSNR diagnostics request CPU frames and may run a parallel
// decoder. They are separate from WebRTC's congestion/quality adaptation.
// Preserve the native capture, codec, bitrate and recovery controls.
const wrappers = new WeakMap();
const measurement = "video-quality-measurement";
const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
let runtime;

const number = (value) => (Number.isFinite(value) && value >= 0 ? value : null);
const text = (value) => (typeof value === "string" && value.length <= 128 ? value : null);

let mappedOpenH264;
function preloadedOpenH264({ env = process.env, readMaps } = {}) {
  if (!readMaps && mappedOpenH264 !== undefined) return mappedOpenH264;
  let selected = null;
  try {
    const maps = readMaps ? readMaps() : require("node:fs").readFileSync("/proc/self/maps", "utf8");
    if (typeof maps === "string" && maps.length <= 4 * 1024 * 1024) {
      const libraries = new Set();
      for (const line of maps.split("\n")) {
        const match = line.match(/^[0-9a-f]+-[0-9a-f]+\s+\S+\s+\S+\s+\S+\s+\d+\s+(\/.*)$/i);
        if (!match || !/\/libopenh264(?:[-.]|$)/.test(match[1])) continue;
        if (match[1].endsWith(" (deleted)")) {
          libraries.add(null);
          continue;
        }
        const filename = match[1].replace(/\\([0-7]{3})/g, (_, octal) =>
          String.fromCharCode(parseInt(octal, 8)),
        );
        libraries.add(filename);
      }
      if (libraries.size === 1) {
        const filename = [...libraries][0];
        if (
          filename &&
          !/[\0\r\n]/.test(filename) &&
          (filename === env.DPE_OPENH264_LIBRARY || filename.includes("/dpe-codecs/openh264/"))
        )
          selected = filename;
      }
    }
  } catch {
    /* No proven preload: keep the native decoder's original path. */
  }
  if (!readMaps) mappedOpenH264 = selected;
  return selected;
}

function withPreloadedOpenH264(options, library) {
  if (!library || !record(options) || !Array.isArray(options.videoDecoders)) return options;
  let changed = false;
  const videoDecoders = options.videoDecoders.map((decoder) => {
    if (
      !record(decoder) ||
      decoder.name !== "H264" ||
      !record(decoder.params) ||
      !["1", 1, true].includes(decoder.params.libopenh264)
    )
      return decoder;
    const previous = decoder.params["libopenh264-path"];
    // Discord's asset manager replaces/cleans this cache independently of the
    // library mapped before sandbox startup. Explicit custom paths stay intact.
    if (
      previous === library ||
      (previous &&
        (typeof previous !== "string" || !previous.includes("/discord_asset_cache/openh264/")))
    )
      return decoder;
    changed = true;
    return { ...decoder, params: { ...decoder.params, "libopenh264-path": library } };
  });
  return changed ? { ...options, videoDecoders } : options;
}

function createTracker({
  capture,
  encoder: encoderGuard,
  settings,
  backends,
  sync,
  portal = () => false,
  x11 = false,
} = {}) {
  const shares = new Set();
  const connections = new Set();
  let sourceTimer,
    sourceKey = "",
    pendingSource = "";
  function sourceSize() {
    const active = [...shares].filter((share) => share.source && share.broadcast !== false);
    const up = capture?.snapshot()?.upstream;
    const width = active.length === 1 && up?.activeStreams === 1 ? up.width : 0;
    const height = active.length === 1 && up?.activeStreams === 1 ? up.height : 0;
    const key = `${width || 0}x${height || 0}`;
    if (key === sourceKey) return;
    if (active.length && pendingSource !== key) {
      pendingSource = key;
      return;
    }
    sourceKey = key;
    if (typeof window !== "undefined" && typeof CustomEvent === "function")
      window.dispatchEvent(
        new CustomEvent("dpe-video-source-size", {
          detail: { width: width || 0, height: height || 0 },
        }),
      );
  }

  backends?.subscribe?.((event) => {
    if (event.event === "started") {
      const active = [...shares].filter((share) => share.source);
      if (active.length === 1) active[0].backendRequestId = event.requestId;
    } else if (event.event === "ended") {
      for (const share of shares)
        if (share.backendRequestId === event.requestId) share.endBackend();
    }
  });
  function updateRate() {
    const active = [...shares].filter((share) => share.source && share.broadcast !== false);
    // A brief overlap also contaminates lifetime deltas after the other share
    // stops. Attribution becomes reliable again only with a new baseline.
    if (active.length > 1) active.forEach((share) => (share.countersReliable = false));
    // A global hook cannot safely impose one share's lower rate on another.
    const rates = active.map((share) => share.pacing && share.fps);
    const rate =
      rates.length && rates.every((fps) => Number.isInteger(fps) && fps > 0 && fps <= 240)
        ? Math.max(...rates)
        : 0;
    if (active.length && !sourceTimer) {
      sourceTimer = setInterval(sourceSize, 500);
      sourceTimer.unref?.();
    } else if (!active.length && sourceTimer) {
      clearInterval(sourceTimer);
      sourceTimer = null;
      sourceSize();
    }
    capture?.setRate(rate);
    if (active.length === 1 && active[0].fps) backends?.setRate(active[0].fps);
  }
  function attach(native) {
    const state = {
      native,
      source: null,
      broadcast: null,
      fps: null,
      codec: null,
      maxBitrate: null,
      generation: 0,
      fallbackCount: 0,
      fallbackObserved: false,
      baseline: null,
      qpBaseline: null,
      statsSequence: 0,
      qpSequence: 0,
      backendRequestId: null,
      backendEnded: false,
      onEnded: null,
    };
    connections.add(state);
    function stop() {
      state.source = null;
      state.backendRequestId = null;
      state.generation++;
      shares.delete(state);
      updateRate();
    }
    state.endBackend = () => {
      if (!state.source || state.backendEnded) return;
      state.backendEnded = true;
      stop();
      try {
        native.clearDesktopSource?.();
      } finally {
        state.onEnded?.();
      }
    };
    return {
      call(name, args, invoke) {
        if (name === "setOnDesktopSourceEnded" && typeof args[0] === "function") {
          const callback = args[0];
          state.onEnded = callback;
          return invoke([
            (...values) => {
              if (state.backendEnded) return;
              stop();
              return callback(...values);
            },
          ]);
        }
        if (name === "setOnVideoEncoderFallbackCallback" && typeof args[0] === "function") {
          const callback = args[0];
          const result = invoke([
            (...values) => {
              state.fallbackCount++;
              return callback(...values);
            },
          ]);
          state.fallbackObserved = true;
          return result;
        }
        // Update only after native accepts a call; preserve all arguments,
        // return values and exceptions. Never retain encryption/source IDs.
        const selecting = name === "setDesktopSource" || name === "setDesktopSourceWithOptions";
        const guardBaseline = selecting ? encoderGuard?.snapshot() : null;
        const result = invoke(args);
        if (name === "setTransportOptions" && record(args[0])) {
          const options = args[0];
          if (Object.hasOwn(options, "captureVideoFrameRate"))
            state.fps = number(options.captureVideoFrameRate);
          if (Object.hasOwn(options, "videoEncoder"))
            state.codec = text(options.videoEncoder?.name);
          if (Object.hasOwn(options, "encodingVideoMaxBitRate"))
            state.maxBitrate =
              options.encodingVideoMaxBitRate > 0 ? number(options.encodingVideoMaxBitRate) : null;
        } else if (name === "setDesktopSource" || name === "setDesktopSourceWithOptions") {
          const options =
            name === "setDesktopSource" ? { sourceId: args[0], type: args[2] } : args[0];
          const prefs = settings?.read() || {};
          if (options?.sourceId == null || options.sourceId === "") {
            stop();
            return result;
          }
          state.source = ["screen", "window"].includes(options?.type) ? options.type : null;
          if (!state.source && options?.sourceId != null) state.source = "unknown";
          state.backendEnded = false;
          state.backendRequestId = null;
          shares.add(state);
          state.portal = portal();
          state.pacing = prefs.capturePacing !== false;
          state.baseline = capture?.snapshot();
          state.guardBaseline = guardBaseline;
          state.countersReliable = true;
          state.generation++;
          state.fallbackCount = 0;
          state.qpBaseline = null;
        } else if (name === "setVideoBroadcast") {
          state.broadcast = args[0] === true;
        } else if (name === "clearDesktopSource" || name === "destroy") {
          stop();
          if (name === "destroy") {
            shares.delete(state);
            connections.delete(state);
          }
        }
        updateRate();
        return result;
      },
    };
  }
  async function outgoingSnapshot() {
    const state = [...shares].find((share) => share.source && share.broadcast !== false);
    if (!state) return { active: false };
    const generation = state.generation;
    const sequence = ++state.statsSequence;
    const report = await new Promise((resolve) => {
      const timer = setTimeout(() => resolve(null), 1000);
      try {
        // Native masks: TRANSPORT=1, OUTBOUND=2. Incoming participant statistics
        // are not needed for this outgoing-share view.
        state.native.getFilteredStats(3, (raw) => {
          clearTimeout(timer);
          try {
            const parsed = JSON.parse(raw);
            resolve({
              video: parsed?.outbound?.video,
              bandwidth: number(parsed?.transport?.sendBandwidth),
            });
          } catch {
            resolve(null);
          }
        });
      } catch {
        clearTimeout(timer);
        resolve(null);
      }
    });
    if (generation !== state.generation || !state.source || state.broadcast === false)
      return { active: false };
    const stats = report?.video;
    const counters = capture?.snapshot();
    const backend = backends?.snapshot();
    const synchronization = sync?.snapshot();
    const guard = encoderGuard?.snapshot();
    const guardRejections = guard
      ? Math.max(
          0,
          (guard.rejectedCreates || 0) +
            (guard.rejectedUpdates || 0) -
            (state.guardBaseline?.rejectedCreates || 0) -
            (state.guardBaseline?.rejectedUpdates || 0),
        )
      : 0;
    const delta = (key) =>
      counters ? Math.max(0, (counters[key] || 0) - (state.baseline?.[key] || 0)) : null;
    const attributable =
      state.countersReliable &&
      [...shares].filter((share) => share.source && share.broadcast !== false).length === 1;
    const observedPipeWire = attributable && counters?.installed && delta("frames") > 0;
    const upstream = counters?.upstream;
    const upstreamApplied =
      upstream?.installed && upstream.activeStreams === 1 && upstream.appliedFps > 0
        ? number(upstream.appliedFps)
        : null;
    const upstreamFps =
      upstream?.installed &&
      upstream.activeStreams === 1 &&
      upstream.negotiatedMaxFpsDenominator > 0
        ? number(upstream.negotiatedMaxFpsNumerator / upstream.negotiatedMaxFpsDenominator)
        : null;
    const encoder = text(stats?.encoderImplementationName);
    const hardware = !encoder
      ? "unknown"
      : /vaapi|nvenc|vulkan|qsv/i.test(encoder)
        ? "hardware"
        : /libvpx|openh264|libaom/i.test(encoder)
          ? "software"
          : "unknown";
    const rendition = stats?.substreams?.find(
      (stream) => !stream.isRTX && !stream.isFlexFEC && stream.width > 0,
    );
    // Native qpSum is cumulative. A lifetime average hides a sudden complex
    // scene after a static desktop, precisely when the quality scaler acts.
    // Never mix codecs, resolutions, encoder resets or out-of-order responses.
    const qpLimit = { H264: 51, AV1: 255, AV1X: 255, VP8: 127 }[stats?.codecName];
    const qpIdentity = JSON.stringify([
      stats?.codecName,
      encoder,
      rendition?.width,
      rendition?.height,
    ]);
    let averageQp = null;
    if (sequence > state.qpSequence) {
      const previous = state.qpBaseline;
      const valid =
        qpLimit &&
        Number.isSafeInteger(stats?.qpSum) &&
        stats.qpSum >= 0 &&
        Number.isSafeInteger(stats?.framesEncoded) &&
        stats.framesEncoded >= 0;
      if (
        valid &&
        previous?.identity === qpIdentity &&
        stats.framesEncoded > previous.frames &&
        stats.qpSum >= previous.sum
      ) {
        const qp = (stats.qpSum - previous.sum) / (stats.framesEncoded - previous.frames);
        if (qp <= qpLimit) averageQp = qp;
      }
      state.qpSequence = sequence;
      state.qpBaseline = valid
        ? { identity: qpIdentity, sum: stats.qpSum, frames: stats.framesEncoded }
        : null;
    }
    return {
      active: true,
      // Native portal routing uses "screen" IDs for both windows and monitors.
      sourceType:
        observedPipeWire && backend
          ? backend.sourceType
          : state.portal || state.source === "unknown"
            ? null
            : state.source,
      requestedFps: state.fps,
      requestedCodec: state.codec,
      actualCodec: text(stats?.codecName),
      encoder,
      hardware,
      // Portal selection alone isn't proof that PipeWire produced a frame.
      captureMethod: observedPipeWire
        ? backend?.method || "pipewire"
        : x11 && state.portal === false && stats?.framesEncoded > 0
          ? "x11"
          : null,
      captureHandoff: observedPipeWire ? backend?.handoff || null : null,
      captureHandoffFallback: observedPipeWire ? backend?.handoffFallback || null : null,
      inputFps: number(stats?.inputFrameRate),
      outputFps: number(stats?.encodeFrameRate),
      encodedFrames:
        Number.isSafeInteger(stats?.framesEncoded) && stats.framesEncoded >= 0
          ? stats.framesEncoded
          : null,
      bitrate: number(stats?.mediaBitrate),
      encoding: {
        configuredMaxBitrate: state.maxBitrate,
        estimatedConnectionBandwidth: report?.bandwidth ?? null,
        averageQp,
        keyFramesEncoded:
          Number.isSafeInteger(rendition?.frameCounts?.keyFrames) &&
          rendition.frameCounts.keyFrames >= 0
            ? rendition.frameCounts.keyFrames
            : null,
        ...Object.fromEntries(
          [
            ["rateLimiterDiscards", "framesDroppedRateLimiter"],
            ["encoderQueueDiscards", "framesDroppedEncoderQueue"],
            ["congestionWindowDiscards", "framesDroppedCongestionWindow"],
          ].map(([name, key]) => [
            name,
            Number.isSafeInteger(stats?.[key]) && stats[key] >= 0 ? stats[key] : null,
          ]),
        ),
      },
      dimensions: { width: number(rendition?.width), height: number(rendition?.height) },
      sourceDimensions:
        observedPipeWire && upstream?.activeStreams === 1
          ? { width: number(upstream.width), height: number(upstream.height) }
          : null,
      fallbackCount: state.fallbackObserved ? state.fallbackCount : null,
      fallbackReason:
        attributable &&
        state.fallbackCount > 0 &&
        guardRejections &&
        guard.reason === "amd-av1-coded-size-padding"
          ? "AV1 cannot preserve this resolution on this AMD GPU."
          : null,
      adaptation: {
        ...Object.fromEntries(
          [
            "cpuLimitedFrameRate",
            "cpuLimitedResolution",
            "bwLimitedFrameRate",
            "bwLimitedResolution",
          ].map((key) => [key, typeof stats?.[key] === "boolean" ? stats[key] : null]),
        ),
        cpuChanges: number(stats?.numberOfCPUAdaptChanges),
        qualityChanges: number(stats?.numberOfQualityAdaptChanges),
      },
      captureCounters: observedPipeWire
        ? {
            frames: delta("frames") + delta("skippedFrames") + delta("pacedFrames"),
            dropped: delta("skippedFrames"),
            paced: delta("pacedFrames"),
            targetFps: counters.pacingFps,
            upstreamAppliedFps: upstreamApplied,
            upstreamFps: upstreamFps || null,
            syncTimelineFrames: delta("syncTimelineFrames"),
            syncTimelineInvalidFrames: delta("syncTimelineInvalidFrames"),
            noSyncTimelineFrames: delta("noSyncTimelineFrames"),
            syncDeadlineCorrection: synchronization?.installed === true,
            mode: counters.pacingFps
              ? "Paced"
              : counters.latestEnabled
                ? "Latest frame"
                : "Observed",
          }
        : null,
    };
  }
  async function snapshot() {
    const outgoing = outgoingSnapshot();
    const watching = [];
    await Promise.all(
      [...connections]
        .filter((state) => !state.source)
        .slice(0, 8)
        .map(
          (state) =>
            new Promise((resolve) => {
              const timer = setTimeout(resolve, 1000);
              try {
                state.native.getFilteredStats(4, (raw) => {
                  clearTimeout(timer);
                  if (!connections.has(state)) return resolve();
                  try {
                    const inbound = JSON.parse(raw)?.inbound;
                    for (const entry of Array.isArray(inbound)
                      ? inbound
                      : Object.values(inbound || {})) {
                      const video = entry?.video;
                      if (
                        !(video?.framesDecoded > 0) ||
                        !(video?.width > 0) ||
                        !(video?.height > 0)
                      )
                        continue;
                      const decoder = text(video.decoderImplementationName);
                      const declared = encoderGuard?.decoderHardware?.(decoder);
                      watching.push({
                        codec: text(video.codecName),
                        decoder,
                        hardware:
                          declared === true
                            ? "hardware"
                            : declared === false
                              ? "software"
                              : "unknown",
                        width: number(video.width),
                        height: number(video.height),
                        fps: number(video.decodeFrameRate),
                      });
                    }
                  } catch {
                    /* A transient or unknown report remains unavailable. */
                  }
                  resolve();
                });
              } catch {
                clearTimeout(timer);
                resolve();
              }
            }),
        ),
    );
    return watching.length
      ? { ...(await outgoing), watching: watching.slice(0, 8) }
      : await outgoing;
  }
  return { attach, snapshot };
}

function install(
  { capture, encoder, settings, voice, backends, sync },
  { platform = process.platform, env = process.env } = {},
) {
  if (runtime || platform !== "linux") return;
  let portalActive = false;
  if (typeof voice?.setNativeDesktopVideoSourcePickerActive === "function") {
    const setActive = voice.setNativeDesktopVideoSourcePickerActive.bind(voice);
    voice.setNativeDesktopVideoSourcePickerActive = (active, ...args) => {
      const result = setActive(active, ...args);
      portalActive = active === true;
      return result;
    };
  }
  const wayland = Boolean(env.WAYLAND_DISPLAY || env.XDG_SESSION_TYPE === "wayland");
  runtime = createTracker({
    capture,
    encoder,
    settings,
    backends,
    sync,
    portal: () => portalActive,
    x11: !wayland && Boolean(env.DISPLAY),
  });
  settings.install({
    snapshot: runtime.snapshot,
    methodReasons: () => backends?.reasons?.() || {},
    methods: () => [
      "auto",
      ...(wayland && typeof voice?.presentNativeScreenSharePicker === "function"
        ? ["portal"]
        : !wayland && env.DISPLAY
          ? ["x11"]
          : []),
      ...(backends?.methods() || []),
    ],
  });
}

function withoutQualityMeasurement(options) {
  if (!record(options)) return options;
  const topLevel =
    typeof options.videoQualityMeasurement === "string" && options.videoQualityMeasurement !== "";
  const encoder = options.videoEncoder;
  const nested =
    record(encoder) &&
    record(encoder.params) &&
    typeof encoder.params[measurement] === "string" &&
    encoder.params[measurement] !== "";
  if (!topLevel && !nested) return options;
  return {
    ...options,
    ...(topLevel ? { videoQualityMeasurement: "" } : {}),
    ...(nested
      ? { videoEncoder: { ...encoder, params: { ...encoder.params, [measurement]: "" } } }
      : {}),
  };
}

function wrap(
  instance,
  {
    platform = process.platform,
    measureQuality = process.env.DPE_VIDEO_QUALITY_MEASUREMENT === "1",
    tracker = runtime,
    openh264Library = platform === "linux" ? preloadedOpenH264() : null,
  } = {},
) {
  if (
    platform !== "linux" ||
    (measureQuality && !tracker && !openh264Library) ||
    typeof instance?.setTransportOptions !== "function"
  )
    return instance;
  if (wrappers.has(instance)) return wrappers.get(instance);
  const observer = tracker?.attach(instance);
  const watched = new Set([
    "setTransportOptions",
    "setDesktopSource",
    "setDesktopSourceWithOptions",
    "clearDesktopSource",
    "setOnDesktopSourceEnded",
    "setVideoBroadcast",
    "destroy",
    "setOnVideoEncoderFallbackCallback",
  ]);
  const proxy = new Proxy(instance, {
    get(target, name) {
      const value = Reflect.get(target, name, target);
      if (typeof value === "function" && watched.has(name))
        return (...args) => {
          if (name === "setTransportOptions") {
            if (!measureQuality) args[0] = withoutQualityMeasurement(args[0]);
            args[0] = withPreloadedOpenH264(args[0], openh264Library);
          }
          const invoke = (values) => Reflect.apply(value, target, values);
          return observer ? observer.call(name, args, invoke) : invoke(args);
        };
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
  wrappers.set(instance, proxy);
  wrappers.set(proxy, proxy);
  return proxy;
}

module.exports = {
  wrap,
  withoutQualityMeasurement,
  withPreloadedOpenH264,
  preloadedOpenH264,
  createTracker,
  install,
};
