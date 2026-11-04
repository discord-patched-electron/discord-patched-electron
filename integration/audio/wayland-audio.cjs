"use strict";
const { systemTool } = require("./system-tools.cjs");
const { spawn } = require("node:child_process");
const path = require("node:path");
const { StringDecoder } = require("node:string_decoder");
const audioPicker = require("./audio-picker.cjs");
const detection = require("./portal-detection.cjs");
const observations = detection.createObserver();
let pickerPending = false;
const listeners = new Set();
function picker(event) {
  pickerPending = event === "begin";
  if (event === "begin") observations.start();
  if (event === "cancel") observations.clear();
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

function disableStreamAudio() {
  const resolve = (name) =>
    require.resolve("./discord-ui/" + name, { paths: [__dirname, path.dirname(__dirname)] });
  const source = require(resolve("source.cjs"));
  return require("electron").webFrame.executeJavaScript(
    source.call(require(resolve("stream-audio.cjs")), { action: "disable" }),
  );
}

// Only the native adapter's active request is authoritative. Discord's generic
// screen token also represents windows and cannot identify an entire monitor.
function optimizedCapture() {
  try {
    return require("./dpe-capture-backends.cjs").snapshot();
  } catch {
    return null;
  }
}

function wrap(
  instance,
  {
    spawnHelper = spawn,
    warn = (message) => console.warn("[dpe-app-audio]", message),
    pickerUI = audioPicker,
    autoDetect = detection.enabled,
    skipScreenPicker = detection.skipScreenPicker,
    disableAudio = disableStreamAudio,
    captureSnapshot = optimizedCapture,
  } = {},
) {
  const nativeTransport = instance.setTransportOptions.bind(instance);
  let helper = null,
    generation = 0,
    requested = 0,
    hint = 0,
    source = false,
    disposed = false,
    readyPid = 0,
    automatic = true,
    skipScreen = true,
    endedCallback;
  let lastState = null;
  let observationTimer;
  let closePicker = null;
  let pickerRequest = null;
  let sourceIdentity = null;
  let audioInitialized = false;
  let pendingAudioState = false;
  let forcePicker = false;
  function dismissPicker() {
    closePicker?.();
    closePicker = null;
    pickerRequest = null;
  }
  function silence() {
    if (!readyPid) return;
    readyPid = 0;
    nativeTransport({ soundsharePid: 0, soundshareLoopback: false });
  }
  function stopHelper() {
    clearInterval(observationTimer);
    dismissPicker();
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
      systemTool("python3"),
      [path.join(__dirname, "dpe-pipewire-audio.py"), "--owner", String(process.pid)],
      { stdio: ["pipe", "pipe", "pipe"] },
    );
    helper = child;
    let lastObservations = "";
    observationTimer = setInterval(() => {
      if (helper !== child || disposed) return;
      const message = JSON.stringify({
        generation,
        capture: captureSnapshot(),
        observations: automatic || skipScreen ? observations.snapshot() : [],
      });
      if (message !== lastObservations) child.stdin.write(message + "\n");
      lastObservations = message;
    }, 400);
    observationTimer.unref?.();
    let buffer = "";
    const decoder = new StringDecoder("utf8");
    child.stdin.on("error", () => {});
    // The helper emits only its tiny protocol. Do not forward arbitrary stderr.
    child.stderr.on("data", (data) => {
      if (process.env.DPE_APP_AUDIO_TRACE === "1")
        for (const line of data.toString().split("\n"))
          if (line.startsWith("[dpe-audio-helper-error] ")) console.warn(line.slice(0, 512));
    });
    child.stdout.on("data", (data) => {
      if (helper !== child || disposed) return;
      buffer += decoder.write(data);
      if (buffer.length > 262144) {
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
        if (value === null || typeof value !== "object" || Array.isArray(value)) continue;
        if (value.generation !== generation || !source || !requested || pickerPending) continue;
        if (value.state === "choosing") {
          silence();
          pickerRequest = value.request;
          const requestGeneration = generation;
          const reply = (selection) => {
            if (
              helper !== child ||
              disposed ||
              generation !== requestGeneration ||
              pickerRequest !== value.request
            )
              return;
            child.stdin.write(
              JSON.stringify({ generation, request: value.request, selection }) + "\n",
            );
            if (selection === "none") {
              // Clear the request as well as the transport so re-enabling the
              // Discord checkbox starts a new selection rather than staying muted.
              requested = 0;
              hint = 0;
              forcePicker = false;
              configure();
              try {
                Promise.resolve(disableAudio())
                  .then((updated) => {
                    if (updated === false)
                      warn(
                        "Audio is off, but Discord's stream-audio setting could not be updated.",
                      );
                  })
                  .catch(() =>
                    warn("Audio is off, but Discord's stream-audio setting could not be updated."),
                  );
              } catch {
                warn("Audio is off, but Discord's stream-audio setting could not be updated.");
              }
            }
          };
          try {
            if (closePicker?.update) closePicker.update(value.options, reply);
            else {
              dismissPicker();
              pickerRequest = value.request;
              closePicker = pickerUI.show(value.options, reply);
            }
          } catch {
            reply("fallback");
          }
          continue;
        }
        dismissPicker();
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
        clearInterval(observationTimer);
        helper = null;
        dismissPicker();
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
    dismissPicker();
    silence();
    if (!source || !requested || pickerPending) {
      stopHelper();
      return;
    }
    audioInitialized = true;
    automatic = autoDetect();
    skipScreen = skipScreenPicker();
    const child = ensureHelper();
    child.stdin.write(
      JSON.stringify({
        generation,
        enabled: true,
        hint,
        autoDetect: automatic,
        skipScreenPicker: skipScreen,
        forcePicker,
        observations: observations.snapshot(),
        capture: captureSnapshot(),
        picker: pickerUI.available() ? "inline" : "native",
      }) + "\n",
    );
  }
  function onPicker(event) {
    // Global picker events also reach ordinary voice/webcam connections. They
    // have no share to reconfigure and must not acquire audio routes.
    if (!source && !requested && !helper) return;
    if (event === "begin" || event === "cancel") {
      sourceIdentity = null;
      audioInitialized = false;
      pendingAudioState = false;
      forcePicker = false;
    }
    if (event === "cancel") source = false;
    configure();
  }
  listeners.add(onPicker);
  instance.setOnDesktopSourceEnded((...args) => {
    if (disposed) return;
    source = false;
    sourceIdentity = null;
    requested = 0;
    audioInitialized = pendingAudioState = forcePicker = false;
    if (!pickerPending) observations.clear();
    configure();
    return endedCallback?.(...args);
  });
  function setSource(method, args, id, type) {
    if (disposed) return;
    const changed =
      !source || !sourceIdentity || sourceIdentity[0] !== id || sourceIdentity[1] !== type;
    if (changed) {
      // A video-source selection gets the user's normal automatic-detection
      // preference. Re-enabling audio on that same video is an explicit choice.
      sourceIdentity = [id, type];
      audioInitialized = pendingAudioState || requested > 0;
      forcePicker = false;
    }
    pendingAudioState = false;
    source = true;
    if (changed || (!helper && requested && !pickerPending)) configure();
    try {
      return method.apply(instance, args);
    } catch (error) {
      // A rejected native source must not leave the previous app audible.
      source = false;
      sourceIdentity = null;
      audioInitialized = pendingAudioState = forcePicker = false;
      configure();
      throw error;
    }
  }
  const methods = {
    setTransportOptions(options) {
      if (disposed) return;
      if (!Object.hasOwn(options, "soundsharePid") && !Object.hasOwn(options, "soundshareLoopback"))
        return nativeTransport(options);
      let changed = false;
      if (Object.hasOwn(options, "soundsharePid")) {
        const value =
          Number.isSafeInteger(options.soundsharePid) && options.soundsharePid > 0
            ? options.soundsharePid
            : 0;
        changed = value !== requested;
        if (changed) {
          if (value === 0) forcePicker = false;
          else if (requested === 0) forcePicker = source && audioInitialized && !pickerPending;
        }
        if (source) audioInitialized = true;
        pendingAudioState = true;
        requested = value;
        hint = value > 1 ? value : 0;
      }
      nativeTransport({
        ...options,
        soundsharePid: changed ? 0 : readyPid,
        soundshareLoopback: false,
      });
      if (changed) {
        readyPid = 0;
        configure();
      }
    },
    setDesktopSource(id, hook, type) {
      return setSource(instance.setDesktopSource, [id, hook, type], id, type);
    },
    setDesktopSourceWithOptions(options) {
      return setSource(
        instance.setDesktopSourceWithOptions,
        [options],
        options?.sourceId,
        options?.type,
      );
    },
    clearDesktopSource() {
      if (disposed) return;
      source = false;
      sourceIdentity = null;
      requested = 0;
      hint = 0;
      audioInitialized = pendingAudioState = forcePicker = false;
      if (!pickerPending) observations.clear();
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
