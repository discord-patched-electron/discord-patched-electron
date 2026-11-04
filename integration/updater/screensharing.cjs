"use strict";
const { randomUUID } = require("node:crypto");
const { normalize, METHODS, CODECS } = require("../video/settings.cjs");

const shortText = (value) =>
  typeof value === "string" && value.length <= 128 ? value.replace(/\p{Cc}/gu, "") : null;
const number = (value) => (Number.isFinite(value) && value >= 0 ? value : null);

function policy(value, env = process.env, platform = process.platform) {
  const wayland = Boolean(env.WAYLAND_DISPLAY || env.XDG_SESSION_TYPE === "wayland");
  return {
    // Selection now belongs to Discord's picker. Old saved method preferences
    // must not divert Vanilla or disabled experimental capture to a helper.
    method: "auto",
    experimentalCapture: normalize(value).experimentalCapture,
    platform,
    wayland,
    x11: !wayland && Boolean(env.DISPLAY),
  };
}

async function apply(value) {
  const { BrowserWindow } = require("electron");
  const { trusted } = require("./presentation.cjs");
  const bridge = require("../discord-ui/source.cjs");
  const source =
    bridge.call(require("../video/capture-policy.cjs"), policy(value)) +
    ";" +
    bridge.call(require("../video/codecs.cjs"), {
      platform: process.platform,
      excludedCodecs: normalize(value).excludedCodecs,
    });
  const results = await Promise.allSettled(
    BrowserWindow.getAllWindows()
      .map((window) => window.webContents)
      .filter(trusted)
      .map((contents) =>
        Promise.resolve().then(() => contents.executeJavaScript(source + "; void 0;")),
      ),
  );
  return results.every((result) => result.status === "fulfilled");
}

function sanitize(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) || value.error) return null;
  const result = { active: typeof value.active === "boolean" ? value.active : null };
  result.codecExclusionError =
    value.codecExclusionError === "no-allowed-encoder" ? value.codecExclusionError : null;
  result.captureHandoff = ["relay", "copy"].includes(value.captureHandoff)
    ? value.captureHandoff
    : null;
  result.captureHandoffFallback = [
    "export-unavailable",
    "synchronization-unavailable",
    "upstream-implicit-sync",
    "cursor-composition",
  ].includes(value.captureHandoffFallback)
    ? value.captureHandoffFallback
    : null;
  for (const name of [
    "captureMethod",
    "sourceType",
    "actualCodec",
    "encoder",
    "requestedCodec",
    "fallbackReason",
  ])
    result[name] = shortText(value[name]);
  for (const name of ["requestedFps", "inputFps", "outputFps", "bitrate", "fallbackCount"])
    result[name] = number(value[name]);
  result.encodedFrames =
    Number.isSafeInteger(value.encodedFrames) && value.encodedFrames >= 0
      ? value.encodedFrames
      : null;
  result.methods = METHODS.filter(
    (method) =>
      method === "auto" || (Array.isArray(value.methods) && value.methods.includes(method)),
  );
  result.methodReasons = {};
  const kmsReason = value.methodReasons?.kms;
  if (
    !result.methods.includes("kms") &&
    [
      "helper-missing",
      "permission-required",
      "permission-blocked",
      "authorization-unavailable",
      "no-outputs",
      "unsupported",
      "native-picker-unavailable",
    ].includes(kmsReason)
  )
    result.methodReasons.kms = kmsReason;
  result.hardware = ["hardware", "software"].includes(value.hardware) ? value.hardware : "unknown";
  result.dimensions = {
    width: number(value.dimensions?.width),
    height: number(value.dimensions?.height),
  };
  result.sourceDimensions = value.sourceDimensions
    ? { width: number(value.sourceDimensions.width), height: number(value.sourceDimensions.height) }
    : null;
  result.captureCounters = null;
  if (value.captureCounters && typeof value.captureCounters === "object") {
    result.captureCounters = {};
    for (const name of [
      "frames",
      "dropped",
      "paced",
      "targetFps",
      "syncTimelineFrames",
      "syncTimelineInvalidFrames",
      "noSyncTimelineFrames",
    ])
      result.captureCounters[name] = number(value.captureCounters[name]);
    result.captureCounters.syncDeadlineCorrection =
      value.captureCounters.syncDeadlineCorrection === true;
    for (const name of ["upstreamFps", "upstreamAppliedFps"])
      result.captureCounters[name] =
        value.captureCounters[name] > 0 ? number(value.captureCounters[name]) : null;
    result.captureCounters.mode = shortText(value.captureCounters.mode);
  }
  result.adaptation = null;
  if (
    value.adaptation &&
    typeof value.adaptation === "object" &&
    !Array.isArray(value.adaptation)
  ) {
    result.adaptation = {};
    for (const name of [
      "cpuLimitedFrameRate",
      "cpuLimitedResolution",
      "bwLimitedFrameRate",
      "bwLimitedResolution",
    ])
      result.adaptation[name] =
        typeof value.adaptation[name] === "boolean" ? value.adaptation[name] : null;
    for (const name of ["cpuChanges", "qualityChanges"])
      result.adaptation[name] =
        Number.isSafeInteger(value.adaptation[name]) && value.adaptation[name] >= 0
          ? value.adaptation[name]
          : null;
  }
  result.encoding = null;
  if (value.encoding && typeof value.encoding === "object" && !Array.isArray(value.encoding)) {
    result.encoding = {};
    for (const name of ["configuredMaxBitrate", "estimatedConnectionBandwidth"])
      result.encoding[name] = number(value.encoding[name]);
    if (!(result.encoding.configuredMaxBitrate > 0)) result.encoding.configuredMaxBitrate = null;
    const qp = number(value.encoding.averageQp);
    result.encoding.averageQp = qp !== null && qp <= 255 ? qp : null;
    for (const name of [
      "rateLimiterDiscards",
      "encoderQueueDiscards",
      "congestionWindowDiscards",
      "keyFramesEncoded",
    ])
      result.encoding[name] =
        Number.isSafeInteger(value.encoding[name]) && value.encoding[name] >= 0
          ? value.encoding[name]
          : null;
  }
  result.watching = Array.isArray(value.watching)
    ? value.watching.slice(0, 8).map((video) => ({
        codec: shortText(video?.codec),
        decoder: shortText(video?.decoder),
        hardware: ["hardware", "software"].includes(video?.hardware) ? video.hardware : "unknown",
        width: number(video?.width),
        height: number(video?.height),
        fps: number(video?.fps),
      }))
    : [];
  return result;
}

let inFlight;
function snapshot({ electron, trusted, timeout = 2000 } = {}) {
  if (inFlight) return inFlight;
  electron ||= require("electron");
  trusted ||= require("./presentation.cjs").trusted;
  const { BrowserWindow, ipcMain } = electron;
  const targets = BrowserWindow.getAllWindows()
    .map((window) => window.webContents)
    .filter((contents) => trusted(contents));
  if (!targets.length) return Promise.resolve({ state: "unavailable", methods: ["auto"] });
  inFlight = new Promise((resolve) => {
    const request = randomUUID();
    const pending = new Set(targets);
    const replies = [];
    let timer;
    const finish = () => {
      clearTimeout(timer);
      ipcMain.removeListener("dpe-screenshare-status-response", receive);
      const active = replies.find((reply) => reply.active === true);
      const current =
        active ||
        replies.find((reply) => reply.watching.length) ||
        replies.find((reply) => reply.active === false) ||
        replies[0];
      resolve(
        current
          ? {
              ...current,
              watching: replies.flatMap((reply) => reply.watching).slice(0, 8),
              state:
                current.active === true ? "active" : current.active === false ? "idle" : "unknown",
            }
          : { state: "unavailable", methods: ["auto"] },
      );
    };
    const receive = (event, id, value) => {
      if (
        id !== request ||
        !pending.has(event.sender) ||
        event.senderFrame !== event.sender.mainFrame ||
        !trusted(event.sender)
      )
        return;
      pending.delete(event.sender);
      const safe = sanitize(value);
      if (safe) replies.push(safe);
      if (!pending.size) finish();
    };
    ipcMain.on("dpe-screenshare-status-response", receive);
    timer = setTimeout(finish, timeout);
    for (const contents of targets) {
      try {
        contents.send("dpe-screenshare-status-request", request);
      } catch {
        pending.delete(contents);
      }
    }
    if (!pending.size) finish();
  }).finally(() => {
    inFlight = undefined;
  });
  return inFlight;
}

async function load(settings) {
  return {
    values: normalize(settings.get("openasar", {}).screensharing),
    runtime: await snapshot(),
  };
}

async function save(settings, patch) {
  if (
    !patch ||
    typeof patch !== "object" ||
    Array.isArray(patch) ||
    Object.entries(patch).some(([key, value]) =>
      key === "captureMethod"
        ? !METHODS.includes(value)
        : key === "excludedCodecs"
          ? !Array.isArray(value) ||
            value.some((codec) => !CODECS.includes(codec)) ||
            new Set(value).size >= CODECS.length
          : !["capturePacing", "experimentalCapture"].includes(key) || typeof value !== "boolean",
    )
  )
    throw new Error("Choose supported screensharing settings.");
  if (patch.captureMethod && patch.captureMethod !== "auto") {
    const current = await snapshot();
    if (!current.methods.includes(patch.captureMethod))
      throw new Error("This capture method is unavailable in the current session.");
  }
  const previous = settings.get("openasar", {});
  const next = { ...previous, screensharing: normalize({ ...previous.screensharing, ...patch }) };
  settings.set("openasar", next);
  try {
    settings.save();
  } catch (error) {
    settings.set("openasar", previous);
    throw error;
  }
  global.oaConfig = { ...next, autoupdate: false };
  if (
    ["captureMethod", "experimentalCapture", "excludedCodecs"].some((key) =>
      Object.hasOwn(patch, key),
    )
  ) {
    let applied = false;
    try {
      applied = await apply(next.screensharing);
    } catch {
      /* The saved choice is still durable. */
    }
    if (!applied)
      return {
        values: next.screensharing,
        error: "Settings saved. Restart Discord to apply them to your next screenshare.",
      };
  }
  return { values: next.screensharing };
}

module.exports = { sanitize, snapshot, load, save, policy };
