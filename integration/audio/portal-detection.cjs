"use strict";
// Observe only screencast portal replies, briefly around the native picker.
// Keep selected app IDs in memory; never retain restore tokens or window titles.
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { StringDecoder } = require("node:string_decoder");

function enabled() {
  return preference("audioAutoDetect");
}
function skipScreenPicker() {
  return preference("audioSkipScreenPicker");
}
function preference(key) {
  if (process.env.DPE_AUDIO_AUTO_DETECT === "0") return false;
  try {
    const profile = process.env.DPE_PROFILE_DIR;
    if (!profile) return true;
    return (
      JSON.parse(fs.readFileSync(path.join(profile, "settings.json"), "utf8"))?.openasar?.[key] !==
      false
    );
  } catch {
    return true;
  }
}
function unwrap(value, depth = 0) {
  if (depth > 12) throw new Error("Nested portal reply");
  if (Array.isArray(value)) return value.map((v) => unwrap(v, depth + 1));
  if (value && typeof value === "object") {
    if (typeof value.type === "string" && Object.hasOwn(value, "data"))
      return unwrap(value.data, depth + 1);
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, unwrap(v, depth + 1)]));
  }
  return value;
}
function observation(message) {
  if (message.payload?.type !== "ua{sv}" || !["method_return", "signal"].includes(message.type))
    return null;
  if (
    message.type === "signal" &&
    (message.interface !== "org.freedesktop.portal.Request" || message.member !== "Response")
  )
    return null;
  const [status, result] = unwrap(message.payload);
  if (status !== 0 || !Array.isArray(result?.streams) || result.streams.length !== 1) return null;
  const [node, properties] = result.streams[0];
  if (!Number.isSafeInteger(node) || node <= 0 || !properties) return null;
  const serial = properties["pipewire-serial"];
  const base = { node, serial: Number.isSafeInteger(serial) && serial > 0 ? String(serial) : null };
  if (properties.source_type === 1) return { ...base, kind: "system", backend: "portal-monitor" };
  if (properties.source_type !== 2) return null; // Regions/virtual sources are not whole screens.
  const restore = result.restore_data;
  if (!Array.isArray(restore) || restore.length !== 3) return null;
  const [provider, version, data] = restore;
  let appId;
  if (provider === "GNOME" && version === 1 && Array.isArray(data?.[2]) && data[2].length === 1) {
    const [, type, window] = data[2][0];
    if (type === 2) appId = window?.[0];
  } else if (provider === "hyprland" && version === 3) {
    appId = data?.windowClass;
  } else if (provider === "KDE" && version === 1 && data?.windows?.length === 1) {
    appId = data.windows[0]?.[0];
  }
  if (typeof appId !== "string" || !appId.trim() || appId.length > 512) return null;
  return { ...base, kind: "application", appId, backend: "portal-" + provider.toLowerCase() };
}
// Mutter exposes exact window IDs even when the portal cannot persist a choice.
function createMutterTracker() {
  const calls = new Map(),
    streams = new Map();
  return (message) => {
    const data = unwrap(message.payload);
    if (
      message.type === "method_call" &&
      message.interface === "org.gnome.Mutter.ScreenCast.Session" &&
      message.member === "RecordWindow"
    ) {
      const id = data?.[0]?.["window-id"];
      if (Number.isSafeInteger(id) && id > 0) {
        calls.set(`${message.sender}:${message.cookie}`, id);
        if (calls.size > 64) calls.delete(calls.keys().next().value);
      }
    } else if (message.type === "method_return" && message.payload?.type === "o") {
      const key = `${message.destination}:${message.reply_cookie}`;
      if (calls.has(key) && typeof data?.[0] === "string") {
        streams.set(`${message.sender}:${data[0]}`, calls.get(key));
        calls.delete(key);
        if (streams.size > 64) streams.delete(streams.keys().next().value);
      }
    } else if (
      message.type === "signal" &&
      message.interface === "org.gnome.Mutter.ScreenCast.Stream" &&
      message.member === "PipeWireStreamAdded"
    ) {
      const windowId = streams.get(`${message.sender}:${message.path}`);
      const node = data?.[0];
      if (windowId && Number.isSafeInteger(node) && node > 0)
        return { node, serial: null, kind: "application", windowId, backend: "mutter-window" };
    }
    return null;
  };
}
function createObserver({ spawnMonitor = spawn, changed = () => {} } = {}) {
  let child,
    timer,
    records = [],
    sequence = 0;
  function stop() {
    clearTimeout(timer);
    const previous = child;
    child = null;
    previous?.kill("SIGTERM");
  }
  function clear() {
    stop();
    records = [];
  }
  function start() {
    clear();
    if (!enabled() && !skipScreenPicker()) return;
    const backends = ["gnome", "kde", "hyprland", "wlr", "cosmic", "gtk", "lxqt"];
    const matches = backends.map(
      (name) => `--match=type='method_return',sender='org.freedesktop.impl.portal.desktop.${name}'`,
    );
    matches.push(
      "--match=type='signal',sender='org.freedesktop.portal.Desktop',interface='org.freedesktop.portal.Request',member='Response'",
    );
    matches.push(
      "--match=type='method_call',destination='org.gnome.Mutter.ScreenCast',interface='org.gnome.Mutter.ScreenCast.Session',member='RecordWindow'",
      "--match=type='method_return',sender='org.gnome.Mutter.ScreenCast'",
      "--match=type='signal',sender='org.gnome.Mutter.ScreenCast',interface='org.gnome.Mutter.ScreenCast.Stream',member='PipeWireStreamAdded'",
    );
    const mutter = createMutterTracker();
    try {
      const monitor = spawnMonitor(
        "/usr/bin/python3",
        [
          path.join(__dirname, "portal-monitor.py"),
          String(process.pid),
          "--user",
          "--json=short",
          ...matches,
          "monitor",
        ],
        { stdio: ["ignore", "pipe", "ignore"] },
      );
      child = monitor;
      let buffer = "";
      const decoder = new StringDecoder("utf8");
      monitor.stdout.on("data", (chunk) => {
        if (child !== monitor) return;
        buffer += decoder.write(chunk);
        if (buffer.length > 262144) return stop();
        let end;
        while ((end = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, end);
          buffer = buffer.slice(end + 1);
          try {
            const message = JSON.parse(line);
            const value = mutter(message) || observation(message);
            if (!value) continue;
            const previous = records.find((item) => item.node === value.node);
            if (previous?.kind === "application" && value.kind === "application") {
              value.windowId ??= previous.windowId;
              value.appId ??= previous.appId;
              if (value.windowId) value.backend = "mutter-window";
            }
            records = records.filter((item) => item.node !== value.node);
            records.push({ ...value, token: ++sequence, observedAt: Date.now() });
            records = records.slice(-32);
            changed();
          } catch {
            /* Unsupported/malformed portal replies leave selection manual. */
          }
        }
      });
      monitor.on("error", () => {
        if (child === monitor) stop();
      });
      monitor.on("exit", () => {
        if (child === monitor) child = null;
      });
      timer = setTimeout(stop, 120000);
      timer.unref?.();
    } catch {
      stop();
    }
  }
  return {
    start,
    stop,
    clear,
    snapshot: () => records.filter((record) => Date.now() - record.observedAt < 15000),
  };
}
module.exports = { enabled, skipScreenPicker, observation, createObserver, createMutterTracker };
