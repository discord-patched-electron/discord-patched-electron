"use strict";
const { ipcRenderer } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const { FrameMeasurements } = require("./frame-markers.cjs");
const { fullPictureEdges } = require("./frame-edges.cjs");
const root = process.env.DPE_SCREENSHARE_OUTPUT;
const config = JSON.parse(fs.readFileSync(path.join(root, "config.json"), "utf8"));
let voice, connection;
let rendered = 0;
let phase = "initial";
const edges = { checked: 0, failed: 0 };
const measurements = new FrameMeasurements({
  sourceClock: () => Number(process.hrtime.bigint() / 1000000n),
  phase: () => phase,
});
async function initialize(endpoint) {
  if (connection) throw Error("Receiver already initialized");
  const directory = path.join(root, "receiver");
  fs.mkdirSync(directory, { recursive: true });
  voice = require(path.join(root, "voice/discord_voice.node"));
  voice.initialize({
    audioSubsystem: "standard",
    logLevel: 1,
    dataDirectory: directory,
    logDirectory: directory,
    maxLogBytes: 2000000,
    useFakeAudioCapture: true,
    useFakeVideoCapture: true,
    offloadAdmControls: true,
    asyncVideoInputDeviceInit: true,
  });
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(Error("Receiver codec survey timed out")), 10000);
    voice.getCodecSurvey(() => {
      clearTimeout(timeout);
      resolve();
    });
  });
  connection = new voice.VoiceConnection(
    "2000",
    { ...endpoint, ssrc: 52, streamParameters: [] },
    () => {},
  );
  connection.setOnVideoDecoderFallbackCallback?.((value) =>
    ipcRenderer.send("receiver-event", "decoder-fallback", value),
  );
  connection.setVideoCodecErrorCallback?.((value) =>
    ipcRenderer.send("receiver-event", "codec-error", value),
  );
  voice.setImageDataAllocator((width, height) => new window.ImageData(width, height));
  connection.setOnVideoCallback((_, __, stream) => {
    voice.setVideoOutputSink(
      stream,
      (image) => {
        rendered++;
        measurements.received(image);
        if (config.content_pattern === "framing") {
          edges.checked++;
          if (!fullPictureEdges(image)) edges.failed++;
        }
        voice.signalVideoOutputSinkReady(stream);
      },
      true,
    );
    voice.signalVideoOutputSinkReady(stream);
  });
}
const allowed = new Set([
  "setTransportOptions",
  "mergeUsers",
  "setDisableLocalVideo",
  "setSelfMute",
  "executeSecureFramesTransition",
]);
ipcRenderer.on("receiver-command", async (_, id, method, args) => {
  try {
    let value = null;
    if (method === "initialize") await initialize(args[0]);
    else if (!connection) throw Error("Receiver is not initialized");
    else if (method === "getFilteredStats")
      value = await new Promise((resolve) => connection.getFilteredStats(args[0], resolve));
    else if (method === "prepareSecureFramesTransition")
      await new Promise((resolve) => connection.prepareSecureFramesTransition(...args, resolve));
    else if (method === "phase") phase = args[0];
    else if (method === "progress") value = rendered;
    else if (method === "output")
      value = { rendered, measurements: measurements.report(), pictureEdges: edges };
    else if (method === "destroy") {
      connection.destroy();
      connection = null;
    } else if (allowed.has(method)) connection[method](...args);
    else throw Error("Unknown receiver command");
    ipcRenderer.send("receiver-result", id, value);
  } catch (error) {
    ipcRenderer.send("receiver-result", id, null, String(error));
  }
});
