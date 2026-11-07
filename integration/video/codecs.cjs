"use strict";
// Page-world adapter. The native capability list and Discord's SFU still decide
// which codec is usable. Preferences only reduce the outgoing share's truthful
// encode capabilities; decoder capabilities and camera calls remain native.
module.exports = function installCodecs(options, runtime) {
  if (options?.platform !== "linux" || !runtime?.watch) return;
  const key = Symbol.for("dpe.videoCodecs");
  const names = ["AV1", "H265", "H264", "VP8"];
  const excluded = names.filter(
    (name) => Array.isArray(options.excludedCodecs) && options.excludedCodecs.includes(name),
  );
  if (globalThis[key]) {
    globalThis[key].excludedCodecs = excluded;
    return;
  }
  const state = {
    installed: 0,
    connections: 0,
    discovery: "idle",
    excludedCodecs: excluded,
    exclusionError: null,
  };
  globalThis[key] = state;
  const wrapped = new WeakSet();
  const discovery = new WeakMap();
  const discovered = new WeakSet();
  function exclusionStatus(reason) {
    state.exclusionError = reason;
    if (typeof globalThis.dispatchEvent === "function" && typeof CustomEvent === "function")
      globalThis.dispatchEvent(new CustomEvent("dpe-codec-exclusion-status", { detail: reason }));
  }
  function restrictShare(connection) {
    // Current native-engine context and ownership fields distinguish sending a
    // Go Live stream from watching one or using the camera in a voice call.
    if (
      connection.context !== "stream" ||
      !connection.userId ||
      connection.streamUserId !== connection.userId
    )
      return;
    exclusionStatus(null);
    const denied = new Set(state.excludedCodecs);
    if (!denied.size) return;
    for (const name of ["codecs", "initialCodecs"]) {
      const descriptor = Object.getOwnPropertyDescriptor(connection, name);
      if (!descriptor?.configurable || !Array.isArray(descriptor.value)) return;
    }
    const filter = (codecs, report) => {
      if (!Array.isArray(codecs)) return codecs;
      const filtered = codecs.map((codec) =>
        codec?.type === "video" && denied.has(codec.name === "AV1X" ? "AV1" : codec.name)
          ? { ...codec, encode: false }
          : codec,
      );
      if (report && filtered.some((codec) => codec.type === "video"))
        exclusionStatus(
          filtered.some((codec) => codec.type === "video" && codec.encode)
            ? null
            : "no-allowed-encoder",
        );
      return filtered;
    };
    for (const name of ["codecs", "initialCodecs"]) {
      // Install before the asynchronous capability callback. Discord advertises
      // this list to the SFU and also reassigns it after fallback/denylist edits.
      // Preserve payload IDs and decode flags; never force an encoder behind the
      // negotiated codec or restore a failed/unsupported/excluded encoder.
      let value = filter(connection[name], name === "codecs");
      Object.defineProperty(connection, name, {
        configurable: true,
        enumerable: true,
        get: () => value,
        set: (next) => {
          value = filter(next, name === "codecs");
        },
      });
    }
  }
  function ready(engine) {
    if (discovered.has(engine)) return Promise.resolve();
    if (discovery.has(engine)) return discovery.get(engine);
    state.discovery = "pending";
    const pending = new Promise((resolve) => {
      let settled = false;
      function finish(status) {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (status === "ready") discovered.add(engine);
        if (discovery.get(engine) === pending) discovery.delete(engine);
        state.discovery = status;
        resolve();
      }
      const timer = setTimeout(() => finish("timeout"), 2000);
      Promise.resolve()
        .then(() => engine.getCodecSurvey())
        // Failed or timed-out probes may retry on the next preparation phase.
        // An unavailable survey must not prevent joining a voice channel.
        .then(
          () => finish("ready"),
          () => finish("unavailable"),
        );
    });
    discovery.set(engine, pending);
    return pending;
  }
  function configure(connection) {
    if (
      !connection ||
      !(connection.experimentFlags instanceof Set) ||
      typeof connection.setExperimentFlag !== "function" ||
      typeof connection.getCodecOptions !== "function" ||
      !Array.isArray(connection.codecs) ||
      connection.codecs.length
    )
      return;
    restrictShare(connection);
    // Explicit native/user codec denylists and mod-filtered capabilities retain
    // precedence. Never restore a codec after a native encoder/decoder failure.
    const denied = String(connection.lastOverrideCodecDenylist || "")
      .split(",")
      .some((codec) => ["AV1", "AV1X"].includes(codec.trim().toUpperCase()));
    if (denied || connection.videoEncoderFallbackPending) return;
    connection.setExperimentFlag("signal_av1_encode", true);
    state.connections++;
  }
  runtime.watch(
    "dpe-video-codecs",
    (source) =>
      [
        "getCodecCapabilities",
        "lastOverrideCodecDenylist",
        "SIGNAL_AV1_ENCODE",
        "hardware-av1-decode",
      ].every((part) => source.includes(part)),
    (exports) => {
      const candidates = Object.values(exports || {}).filter(
        (value) =>
          typeof value === "function" &&
          typeof value.prototype?.connect === "function" &&
          typeof value.prototype?.getCodecSurvey === "function" &&
          typeof value.prototype?.fetchAsyncResources === "function" &&
          typeof value.prototype?.createReplayConnection === "function",
      );
      if (candidates.length !== 1) return;
      const prototype = candidates[0].prototype;
      if (wrapped.has(prototype)) return;
      const connect = prototype.connect;
      const fetchResources = prototype.fetchAsyncResources;
      prototype.fetchAsyncResources = function (...args) {
        const resources = Reflect.apply(fetchResources, this, args);
        // RTCConnection already awaits this hook before voice Identify and
        // native connection creation. A null survey still completes discovery.
        return Promise.all([resources, ready(this)]).then(([value]) => value);
      };
      prototype.connect = function (...args) {
        const connection = Reflect.apply(connect, this, args);
        try {
          // connect() returns before native getCodecCapabilities calls back.
          // Its existing capability intersection assigns payload IDs/priorities
          // and advertises them normally. Do not rewrite the final encoder.
          configure(connection);
        } catch (error) {
          console.warn("[DPE video] Codec preference unavailable", error);
        }
        return connection;
      };
      wrapped.add(prototype);
      state.installed++;
    },
  );
};
