const { ipcRenderer } = require("electron");
const fs = require("node:fs"),
  path = require("node:path");
const output = process.env.DPE_AUDIO_OUTPUT;
const inputs = JSON.parse(
  fs.readFileSync(path.join(output, "inputs.json"), "utf8"),
);
globalThis.window.DiscordNative = {
  isRenderer: true,
  settings: { getSync: (k, v) => (k === "offloadAdmControls" ? false : v) },
  features: { declareSupported() {} },
  processUtils: {
    // The container has only our private virtual audio server, no host devices.
    // Real enumeration is required to catch internal-sink notification leaks.
    getMainArgvSync: () => ["fixture"],
  },
  fileManager: {
    getModuleDataPathSync: () => output,
    getAndCreateLogDirectorySync: () => output,
  },
};
// Represents received Discord playback. System sharing must exclude this
// descendant as well as the private bridge's own playback stream.
const discordPlayback = require("node:child_process").spawn(
  "/usr/bin/pw-cat",
  ["--playback", "--target=test-sink", path.join(output, "discord.wav")],
  { stdio: "ignore" },
);
const rawVoice = require(path.join(output, "voice/discord_voice.node"));
const rawOutputDevices = rawVoice.getOutputDevices.bind(rawVoice);
const voice = require(path.join(output, "voice"));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function stage(value) {
  fs.writeFileSync(path.join(output, 'stage.json'), JSON.stringify({stage:value}));
}
function devices(get, label) {
  stage(label);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`${label} timed out`)), 8000);
    get(value => { clearTimeout(timer); resolve(value); });
  });
}
const events = [];
const privateDevice = d => /^dpe-audio-[a-f0-9]{32}-(mix|native)(\.monitor)?$/.test(d.guid || d.name || '');
let internalDeviceControl = false, hiddenDeviceLeaks = 0;
voice.setDeviceChangeCallback((inputs, outputs) => {
  hiddenDeviceLeaks += [...inputs, ...outputs].filter(privateDevice).length;
});
let phase = null,
  connection,
  pickerResult;
voice.setNativeScreenSharePickerCallbacks(
  (...args) => {
    events.push({ event: "selected" });
    connection.setDesktopSourceWithOptions({
      sourceId: "screen:0:0",
      type: "screen",
    });
    pickerResult?.("selected");
  },
  () => {
    events.push({ event: "cancelled" });
    pickerResult?.("cancelled");
  },
  (error) => {
    events.push({ event: "error", error });
    pickerResult?.(
      error === "Portal request was cancelled" ? "cancelled" : "error",
    );
  },
);
voice.setNativeDesktopVideoSourcePickerActive(true);
const pcmFormats = {};
voice.startRecordingRawSamples((samples, channels, rate) => {
  const key = [phase, channels, rate, samples.length].join(":");
  pcmFormats[key] = (pcmFormats[key] || 0) + 1;
  if (phase && rate === 48000)
    fs.appendFileSync(
      path.join(output, phase + ".raw"),
      Buffer.from(new Int16Array(samples).buffer),
    );
});
async function choose(selection) {
  phase = null;
  connection.clearDesktopSource();
  voice.releaseNativeDesktopVideoSourcePickerStream();
  await wait(300);
  fs.writeFileSync(
    path.join(output, "selection.json"),
    JSON.stringify(selection),
  );
  const explicitSelection = ["interactive", "manual"].includes(process.env.DPE_FIXTURE_SELECTION);
  const done = path.join(output, "audio-choice-done");
  if (explicitSelection) fs.rmSync(done, { force: true });
  const result = await new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error("Native portal selection timed out")),
      10000,
    );
    pickerResult = (value) => {
      clearTimeout(timer);
      resolve(value);
    };
    voice.presentNativeScreenSharePicker(
      selection.kind === "system" ? "screen" : "window",
    );
  });
  if (result === "error") throw new Error("Native picker failed");
  connection.setTransportOptions({
    soundsharePid: 1,
    soundshareLoopback: true,
  });
  if (explicitSelection && result === "selected") {
    const deadline = Date.now() + 90000;
    while (!fs.existsSync(done)) {
      if (Date.now() > deadline)
        throw new Error("Operator selection timed out");
      await wait(100);
    }
  }
  return result;
}
async function capture(name, change, settle = 2000) {
  stage(name + ':select');
  phase = null;
  await change();
  await wait(settle);
  if (name === 'app-a') {
    const raw = await devices(rawOutputDevices, 'raw output enumeration');
    const filtered = await devices(voice.getOutputDevices.bind(voice), 'filtered output enumeration');
    fs.writeFileSync(path.join(output, 'device-control.json'), JSON.stringify({raw, filtered}));
    internalDeviceControl = raw.some(privateDevice);
    hiddenDeviceLeaks += filtered.filter(privateDevice).length;
  }
  phase = name;
  stage(name + ':record');
  await wait(1600);
  phase = null;
}
(async () => {
  connection = voice.createVoiceConnectionWithOptions(
    "1234",
    { ssrc: 42, address: "127.0.0.1", port: 50000, streamParameters: [] },
    () => {},
  );
  connection.setDesktopSourceStatusCallback((value) =>
    events.push({ event: "video-status", value }),
  );
  await capture("app-a", () => choose({ pid: inputs.a.pid, kind: "window" }));
  await capture("app-b", () => choose({ pid: inputs.b.pid, kind: "window" }));
  await capture("system", () => choose({ kind: "system" }));
  await capture("disabled", () =>
    connection.setTransportOptions({ soundsharePid: 0 }),
  );
  await capture("audio-cancelled", async () => {
    await choose({ pid: inputs.a.pid, kind: "window", no_audio: true });
    if (!["manual", "interactive"].includes(process.env.DPE_FIXTURE_SELECTION))
      connection.setTransportOptions({ soundsharePid: 0 });
  });
  await capture("cancelled", async () => {
    if ((await choose({ kind: "cancel" })) !== "cancelled")
      throw new Error("Portal did not cancel");
  });
  await capture("re-enabled", () =>
    choose({ pid: inputs.a.pid, kind: "window" }),
  );
  await capture("app-exited", () => process.kill(inputs.a.pid, "SIGTERM"));
  await capture("cleared", () => {
    connection.clearDesktopSource();
    voice.releaseNativeDesktopVideoSourcePickerStream();
  });
  discordPlayback.kill("SIGTERM");
  voice.stopRecordingRawSamples();
  connection.destroy();
  await wait(700);
  const frames = events
    .filter((e) => e.event === "video-status")
    .map((e) => e.value);
  if (
    !frames.some((v) => v.pipewireFrames > 0) ||
    frames.some((v) => v.x11Frames > 0)
  )
    throw new Error("Expected actual Wayland/PipeWire video frames");
  if (!internalDeviceControl || hiddenDeviceLeaks) throw new Error('Internal audio devices leaked or positive control missing');
  ipcRenderer.send("audio-result", {
    passed: true,
    events,
    wayland_video_verified: true,
    internal_device_control: internalDeviceControl,
    hidden_device_leaks: hiddenDeviceLeaks,
    compositor: "niri",
    pcmFormats,
    portal: "controlled test portal backed by real niri screencasts",
    real_discord_call_verified: false,
  });
})().catch((error) =>
  ipcRenderer.send("audio-result", {
    passed: false,
    error: error.stack,
    events,
  }),
);
