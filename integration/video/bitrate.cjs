"use strict";

// Optionally preserve the share ceiling when viewer pixel wants change. The
// optional higher ceiling still travels through Discord quality and signaling.
module.exports = function installBitrate(options, runtime) {
  if (options?.platform !== "linux" || !runtime?.watch) return;
  const value = options.maxBitrate;
  const maximum = Number.isSafeInteger(value) && value > 9000000 && value <= 32000000 ? value : 0;
  const key = Symbol.for("dpe.videoBitrate");
  if (globalThis[key]) {
    globalThis[key].requestedMaxBitrate = maximum;
    globalThis[key].preserveViewerBitrate = options.preserveViewerBitrate === true;
    return;
  }
  const state = {
    requestedMaxBitrate: maximum,
    preserveViewerBitrate: options.preserveViewerBitrate === true,
    constantsReviewed: false,
    installed: 0,
    connections: 0,
    uplifts: 0,
    callMaxUplifts: 0,
    preservedCeilings: 0,
  };
  globalThis[key] = state;
  const engines = new WeakSet();
  const connections = new Set();
  let sourceSize = null,
    pendingSize = "";
  globalThis.addEventListener?.("dpe-video-source-size", (event) => {
    if (typeof event.detail !== "string" || event.detail.length > 128) return;
    let dimensions;
    try {
      dimensions = JSON.parse(event.detail);
    } catch {
      return;
    }
    const { width, height } = dimensions || {};
    const size =
      Number.isInteger(width) &&
      Number.isInteger(height) &&
      width > 0 &&
      height > 0 &&
      width <= 16384 &&
      height <= 16384
        ? { width, height }
        : null;
    const key = size ? `${width}x${height}` : "";
    if (pendingSize === key) return;
    pendingSize = key;
    sourceSize = size;
    for (const connection of connections) {
      if (connection.destroyed) {
        connections.delete(connection);
        continue;
      }
      const last = connection.lastDesktopEncodingOptions;
      if (owned(connection) && connection.hasDesktopSource?.() && last) {
        const update = connection.updateVideoQuality;
        const body = typeof update === "function" ? update.toString().replace(/\s+/g, "") : "";
        // Reapplying the same selection is a native no-op. Refresh the reviewed
        // constraints and stream parameters together when source size changes.
        if (
          [
            "this.applyQualityConstraints(",
            "this.videoStreamParameters",
            "streamParameters",
            "this.updateVideoQualityCore(",
          ].every((part) => body.includes(part))
        )
          Reflect.apply(update, connection, []);
      }
    }
  });
  const owned = (connection) =>
    connection?.context === "stream" &&
    Boolean(connection.userId) &&
    connection.streamUserId === connection.userId;
  function reviewed(connection) {
    if (
      typeof connection.setDesktopEncodingOptions !== "function" ||
      typeof connection.getConnectionTransportOptions !== "function" ||
      typeof connection.setCalcMaxBitrateFunc !== "function"
    )
      return false;
    // Bind null-result handling to the actual reviewed default expression.
    // Unknown upstream policies remain unchanged rather than assuming 9 Mbps.
    const desktop = connection.setDesktopEncodingOptions.toString().replace(/\s+/g, "");
    const parameters = /^setDesktopEncodingOptions\(([\w$]+),([\w$]+),([\w$]+)\)\{/.exec(desktop);
    const fallback =
      /null==([\w$]+)&&\(\1=0===([\w$]+)&&([\w$]+)>=10\|\|\2>720\|\|\3>30\?([\w$]+)\.oL:\4\.lo\)/.exec(
        desktop,
      );
    const transport = connection.getConnectionTransportOptions.toString().replace(/\s+/g, "");
    return (
      parameters &&
      fallback &&
      fallback[2] === parameters[2] &&
      fallback[3] === parameters[3] &&
      desktop.includes("this.calcMaxBitrateFunc(") &&
      desktop.includes("this.videoStreamParameters") &&
      desktop.includes("this.videoQualityManager.setGoliveQuality(") &&
      /callMaxBitRate:[\w$]+\.us[,}]/.test(transport) &&
      transport.includes("encodingVideoDegradationPreference:")
    );
  }
  function eligible(connection, request) {
    if (!owned(connection) || !state.constantsReviewed || !state.requestedMaxBitrate) return false;
    const { width, height, framerate, videoCodec } = request || {};
    return (
      ["H264", "H265", "AV1", "AV1X", "VP8"].includes(videoCodec) &&
      Number.isSafeInteger(width) &&
      width >= 0 &&
      Number.isSafeInteger(height) &&
      height >= 0 &&
      Number.isSafeInteger(framerate) &&
      framerate > 0 &&
      framerate <= 240 &&
      ((height === 0 && framerate >= 10) || height > 720 || framerate > 30)
    );
  }
  function preserveCeiling(connection) {
    const manager = connection.videoQualityManager;
    const original = manager?.getGoliveQuality;
    if (typeof original !== "function") return;
    const body = original.toString().replace(/\s+/g, "");
    // Reviewed pixel-wants policy scales the *application maximum* linearly
    // with receiver pixels. Congestion control separately owns the allocation.
    if (
      ![
        "this.goliveMaxQuality.encode.pixelCount",
        "this.fakeGoLiveEncodePixelCount",
        "this.scaleLinearly(",
        "this.goliveMaxQuality.bitrateMax",
        "bitrateMax:Math.max(Math.ceil(",
        "encode:this.goliveMaxQuality.encode",
        "capture:this.goliveMaxQuality.capture",
      ].every((part) => body.includes(part))
    )
      return;
    manager.getGoliveQuality = function (...args) {
      const quality = Reflect.apply(original, this, args);
      const ceiling = this.goliveMaxQuality;
      const encode = ceiling?.encode;
      if (
        owned(connection) &&
        state.constantsReviewed &&
        quality &&
        encode &&
        Number.isFinite(args[1]) &&
        (args[1] > 0 || sourceSize) &&
        (encode.height > 720 || encode.framerate > 30) &&
        Number.isSafeInteger(ceiling.bitrateMax) &&
        ceiling.bitrateMax > 0 &&
        Number.isFinite(quality.bitrateMax) &&
        quality.bitrateMax <= ceiling.bitrateMax
      ) {
        if (!state.preserveViewerBitrate) {
          if (!sourceSize || !(encode.pixelCount > 0) || !(args[1] > 0)) return quality;
          const pixels = Math.min(encode.pixelCount, sourceSize.width * sourceSize.height);
          if (pixels === encode.pixelCount) return quality;
          // The selected raster is an upper bound, not the available source.
          // Reuse Discord's reviewed viewer-demand policy with the source as
          // its denominator, preserving the negotiated cap and native geometry.
          const policy = Object.create(this);
          Object.defineProperty(policy, "goliveMaxQuality", {
            value: { ...ceiling, encode: { ...encode, pixelCount: pixels } },
          });
          let sourceQuality;
          try {
            sourceQuality = Reflect.apply(original, policy, args);
          } catch {
            // A policy that requires private receiver state keeps native behavior.
            return quality;
          }
          if (
            !Number.isFinite(sourceQuality?.bitrateMax) ||
            sourceQuality.bitrateMax < 0 ||
            sourceQuality.bitrateMax > ceiling.bitrateMax
          )
            return quality;
          return Object.assign(Object.create(Object.getPrototypeOf(quality)), quality, {
            bitrateMax: sourceQuality.bitrateMax,
          });
        }
        state.preservedCeilings++;
        // Budget the available source raster at the selected encode quality.
        // Receiver pixel wants do not change this policy's encode dimensions,
        // so using them here can starve a full-size encoder behind a small tile.
        // Native congestion control still owns the actual allocation.
        let maximum = ceiling.bitrateMax;
        if (sourceSize) {
          const selected =
            encode.pixelCount > 0 ? encode.pixelCount : sourceSize.width * sourceSize.height;
          const pixels = Math.min(selected, sourceSize.width * sourceSize.height);
          const budget =
            9000000 *
            Math.sqrt(pixels / (1920 * 1080)) *
            Math.sqrt(Math.min(240, Math.max(1, encode.framerate || 30)) / 60);
          maximum = Math.min(
            maximum,
            Math.max(ceiling.bitrateMin || 0, 150000, Math.ceil(budget / 250000) * 250000),
          );
        }
        // Retain native capture, receiver wants, startup target and minimum.
        // The ceiling is headroom, never a forced target or padding demand.
        return Object.assign(Object.create(Object.getPrototypeOf(quality)), quality, {
          bitrateMax: maximum,
        });
      }
      return quality;
    };
  }
  function configure(connection) {
    if (!owned(connection) || connections.has(connection) || !reviewed(connection)) return;
    preserveCeiling(connection);
    const set = connection.setCalcMaxBitrateFunc;
    const transport = connection.getConnectionTransportOptions;
    connection.setCalcMaxBitrateFunc = function (callback, ...args) {
      if (typeof callback !== "function") return Reflect.apply(set, this, [callback, ...args]);
      return Reflect.apply(set, this, [
        function (...values) {
          const original = Reflect.apply(callback, this, values);
          // Keep explicit lower limits, entitlement experiments and mod choices.
          if ((original == null || original === 9000000) && eligible(this, values[0])) {
            state.uplifts++;
            return state.requestedMaxBitrate;
          }
          return original;
        },
        ...args,
      ]);
    };
    connection.getConnectionTransportOptions = function (...args) {
      const original = Reflect.apply(transport, this, args);
      if (
        owned(this) &&
        state.constantsReviewed &&
        state.requestedMaxBitrate &&
        original?.callMaxBitRate === 10000000
      ) {
        state.callMaxUplifts++;
        // Retain the native 1 Mbps margin above the video ceiling for audio and
        // transport overhead. Start/minimum rates and congestion stay native.
        return { ...original, callMaxBitRate: state.requestedMaxBitrate + 1000000 };
      }
      return original;
    };
    connections.add(connection);
    state.connections++;
  }
  runtime.watch(
    "dpe-video-bitrate-constants",
    (source) =>
      ["encodingVideoMaxBitRate", "desktopBitrate", "videoBitrateFloor"].every((name) =>
        source.includes(name),
      ),
    (exports) => {
      if (exports?.oL === 9000000 && exports?.lo === 3500000 && exports?.us === 10000000)
        state.constantsReviewed = true;
    },
  );
  runtime.watch(
    "dpe-video-bitrate",
    (source) =>
      [
        "getCodecCapabilities",
        "lastOverrideCodecDenylist",
        "SIGNAL_AV1_ENCODE",
        "hardware-av1-decode",
      ].every((name) => source.includes(name)),
    (exports) => {
      const candidates = Object.values(exports || {}).filter(
        (value) =>
          typeof value === "function" &&
          typeof value.prototype?.connect === "function" &&
          typeof value.prototype?.getCodecSurvey === "function" &&
          typeof value.prototype?.fetchAsyncResources === "function" &&
          typeof value.prototype?.createReplayConnection === "function",
      );
      if (candidates.length !== 1 || engines.has(candidates[0].prototype)) return;
      const prototype = candidates[0].prototype;
      const connect = prototype.connect;
      prototype.connect = function (...args) {
        const connection = Reflect.apply(connect, this, args);
        try {
          // Native initialization continues asynchronously. RTCConnection sets
          // the policy callback before its first Go Live quality selection.
          configure(connection);
        } catch (error) {
          console.warn("[DPE video] Bitrate experiment unavailable", error);
        }
        return connection;
      };
      engines.add(prototype);
      state.installed++;
    },
  );
};
