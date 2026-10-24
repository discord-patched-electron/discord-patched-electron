"use strict";
const { spawn } = require("node:child_process");
const path = require("node:path");
let pickerPending = false;
const listeners = new Set();
function picker(event) {
  pickerPending = event === "begin";
  for (const listener of listeners) listener(event);
}
const installed = new WeakSet();
function installVoice(voice) {
  if (installed.has(voice) || process.env.DPE_APP_AUDIO === "0") return;
  installed.add(voice);
  // The native game-capture stream otherwise requests PulseAudio's two-second
  // default fragment size. Keep its buffer usable for live sharing, and respect
  // an explicit user setting. This changes no server defaults or devices.
  process.env.PULSE_LATENCY_MSEC ??= "40";
  const present = voice.presentNativeScreenSharePicker.bind(voice);
  voice.presentNativeScreenSharePicker = (...args) => {
    picker("begin");
    try {
      return present(...args);
    } catch (error) {
      picker("cancel");
      throw error;
    }
  };
  const callbacks = voice.setNativeScreenSharePickerCallbacks.bind(voice);
  voice.setNativeScreenSharePickerCallbacks = (selected, cancelled, failed) =>
    callbacks(
      (...args) => {
        picker("selected");
        return selected?.(...args);
      },
      (...args) => {
        picker("cancel");
        return cancelled?.(...args);
      },
      (...args) => {
        picker("cancel");
        return failed?.(...args);
      },
    );
  const release = voice.releaseNativeDesktopVideoSourcePickerStream.bind(voice);
  voice.releaseNativeDesktopVideoSourcePickerStream = (...args) => {
    picker("cancel");
    return release(...args);
  };
}

function wrap(
  instance,
  { spawnHelper = spawn, warn = (message) => console.warn("[dpe-app-audio]", message) } = {},
) {
  const nativeTransport = instance.setTransportOptions.bind(instance);
  let helper = null,
    generation = 0,
    requested = 0,
    hint = 0,
    source = false,
    disposed = false,
    readyPid = 0,
    endedCallback;
  let lastState = null;
  function silence() {
    readyPid = 0;
    nativeTransport({ soundsharePid: 0, soundshareLoopback: false });
  }
  function stopHelper() {
    if (!helper) return;
    const previous = helper;
    helper = null;
    previous.stdin.end();
    const timer = setTimeout(() => previous.kill("SIGTERM"), 5000);
    timer.unref?.();
    previous.once("exit", () => clearTimeout(timer));
  }
  function ensureHelper() {
    if (helper) return helper;
    const child = spawnHelper(
      "/usr/bin/python3",
      [path.join(__dirname, "dpe-pipewire-audio.py"), "--owner", String(process.pid)],
      { stdio: ["pipe", "pipe", "pipe"] },
    );
    helper = child;
    let buffer = "";
    child.stdin.on("error", () => {});
    // The helper emits only its tiny protocol. Do not forward arbitrary stderr.
    child.stderr.on("data", (data) => {
      if (process.env.DPE_APP_AUDIO_TRACE === "1")
        for (const line of data.toString().split("\n"))
          if (line.startsWith("[dpe-audio-helper-error] ")) console.warn(line.slice(0, 512));
    });
    child.stdout.on("data", (data) => {
      if (helper !== child || disposed) return;
      buffer += data.toString();
      if (buffer.length > 65536) {
        silence();
        stopHelper();
        return;
      }
      let index;
      while ((index = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, index);
        buffer = buffer.slice(index + 1);
        let value;
        try {
          value = JSON.parse(line);
        } catch {
          continue;
        }
        if (value.generation !== generation || !source || !requested || pickerPending) continue;
        const pid =
          value.state === "ready" && Number.isSafeInteger(value.pid) && value.pid > 1
            ? value.pid
            : 0;
        if (pid !== readyPid) {
          readyPid = pid;
          nativeTransport({ soundsharePid: pid, soundshareLoopback: false });
        }
        if (value.state === "unavailable" && lastState !== value.state)
          warn(
            "Application audio is unavailable; check that PipeWire and the audio helper tools are installed.",
          );
        lastState = value.state;
      }
    });
    const failed = () => {
      if (helper === child) {
        helper = null;
        if (!disposed) silence();
      }
    };
    child.on("error", failed);
    child.on("exit", failed);
    return child;
  }
  function configure() {
    if (disposed) return;
    ++generation;
    silence();
    if (!source || !requested || pickerPending) {
      stopHelper();
      return;
    }
    const child = ensureHelper();
    child.stdin.write(JSON.stringify({ generation, enabled: true, hint }) + "\n");
  }
  function onPicker(event) {
    // Global picker events also reach ordinary voice/webcam connections. They
    // have no share to reconfigure and must not acquire audio routes.
    if (!source && !requested && !helper) return;
    if (event === "cancel") source = false;
    configure();
  }
  listeners.add(onPicker);
  instance.setOnDesktopSourceEnded((...args) => {
    if (disposed) return;
    source = false;
    requested = 0;
    configure();
    return endedCallback?.(...args);
  });
  function setSource(method, args) {
    if (disposed) return;
    source = true;
    configure();
    try {
      return method.apply(instance, args);
    } catch (error) {
      // A rejected native source must not leave the previous app audible.
      source = false;
      configure();
      throw error;
    }
  }
  const methods = {
    setTransportOptions(options) {
      if (disposed) return;
      if (
        !source &&
        !requested &&
        !helper &&
        !Object.hasOwn(options, "soundsharePid") &&
        !Object.hasOwn(options, "soundshareLoopback")
      )
        return nativeTransport(options);
      let changed = false;
      if (Object.hasOwn(options, "soundsharePid")) {
        const value =
          Number.isSafeInteger(options.soundsharePid) && options.soundsharePid > 0
            ? options.soundsharePid
            : 0;
        changed = value !== requested;
        requested = value;
        hint = value > 1 ? value : 0;
      }
      if (changed) readyPid = 0;
      nativeTransport({
        ...options,
        soundsharePid: readyPid,
        soundshareLoopback: false,
      });
      if (changed) configure();
    },
    setDesktopSource(id, hook, type) {
      return setSource(instance.setDesktopSource, [id, hook, type]);
    },
    setDesktopSourceWithOptions(options) {
      return setSource(instance.setDesktopSourceWithOptions, [options]);
    },
    clearDesktopSource() {
      if (disposed) return;
      source = false;
      requested = 0;
      hint = 0;
      configure();
      return instance.clearDesktopSource();
    },
    setOnDesktopSourceEnded(callback) {
      endedCallback = callback;
    },
    destroy() {
      if (disposed) return;
      silence();
      disposed = true;
      listeners.delete(onPicker);
      stopHelper();
      return instance.destroy();
    },
  };
  return new Proxy(instance, {
    get(target, name) {
      if (Object.hasOwn(methods, name)) return methods[name];
      const value = Reflect.get(target, name, target);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}
module.exports = { installVoice, wrap };
