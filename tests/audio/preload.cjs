const fs = require("node:fs"),
  path = require("node:path");
const { ipcRenderer } = require("electron");
const output = process.env.DPE_AUDIO_OUTPUT;
const inputs = JSON.parse(fs.readFileSync(path.join(output, "inputs.json"), "utf8"));
globalThis.window.DiscordNative = {
  isRenderer: true,
  settings: { getSync: (key, value) => (key === "offloadAdmControls" ? false : value) },
  features: { declareSupported() {} },
  processUtils: {
    getMainArgvSync: () => ["fixture", "--use-fake-audio-capture", "--use-fake-video-capture"],
  },
  fileManager: { getModuleDataPathSync: () => output, getAndCreateLogDirectorySync: () => output },
};
const voice = require(path.join(output, "voice"));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const events = [];
(async () => {
  const native = new voice.VoiceConnection(
    "1234",
    { ssrc: 42, address: "127.0.0.1", port: 50000, streamParameters: [] },
    () => {},
  );
  let connection;
  let phase = null;
  voice.startRecordingRawSamples((samples, channels, rate) => {
    if (phase && (rate === 48000 || rate === 8000)) {
      const data = new Int16Array(samples);
      fs.appendFileSync(
        path.join(output, phase + (rate === 8000 ? "-microphone" : "") + ".raw"),
        Buffer.from(data.buffer),
      );
    }
  });
  native.setOnSoundshare((value) => events.push({ event: "soundshare", value }));
  native.setOnSoundshareFailed((...value) => events.push({ event: "failure", value }));
  async function capture(name, change, settle = 700) {
    phase = null;
    change();
    await wait(settle);
    phase = name;
    await wait(1500);
    phase = null;
  }
  // Positive control demonstrates that the uncorrected system PID records both apps.
  await capture("system", () =>
    native.setTransportOptions({ soundsharePid: 1, soundshareLoopback: false }),
  );
  native.destroy();
  await wait(200);
  connection = voice.createVoiceConnectionWithOptions(
    "5678",
    { ssrc: 43, address: "127.0.0.1", port: 50001, streamParameters: [] },
    () => {},
  );
  await capture("app-a", () => {
    connection.setDesktopSourceWithOptions({ sourceId: inputs.a.sourceId, type: "window" });
    connection.setTransportOptions({ soundsharePid: 1, soundshareLoopback: true });
  });
  await capture("app-b", () =>
    connection.setDesktopSourceWithOptions({ sourceId: inputs.b.sourceId, type: "window" }),
  );
  await capture("disabled", () => connection.setTransportOptions({ soundsharePid: 0 }));
  await capture("unknown", () => {
    connection.setDesktopSourceWithOptions({ sourceId: "window:999999999:0", type: "window" });
    connection.setTransportOptions({ soundsharePid: 1, soundshareLoopback: true });
  });
  await capture("re-enabled", () => {
    connection.setDesktopSourceWithOptions({ sourceId: inputs.a.sourceId, type: "window" });
    connection.setTransportOptions({ soundsharePid: 1 });
  });
  await capture("app-exited", () => process.kill(inputs.a.pid, "SIGTERM"), 1600);
  await capture("cleared", () => connection.clearDesktopSource());
  voice.stopRecordingRawSamples();
  connection.destroy();
  await wait(200);
  ipcRenderer.send("audio-result", {
    passed: true,
    events,
    cases: [
      "system",
      "app-a",
      "app-b",
      "disabled",
      "unknown",
      "re-enabled",
      "app-exited",
      "cleared",
    ],
  });
})().catch((error) => ipcRenderer.send("audio-result", { passed: false, error: error.stack }));
