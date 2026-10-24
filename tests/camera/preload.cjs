const { ipcRenderer } = require("electron");
const output = process.env.DPE_CAMERA_OUTPUT;
let voice,
  selected,
  result = {};
(async () => {
  const start = performance.now();
  voice = require(process.env.DPE_CAMERA_VOICE_MODULE);
  voice.initialize({
    audioSubsystem: "standard",
    logLevel: 2,
    dataDirectory: output,
    logDirectory: output,
    maxLogBytes: 1000000,
    useFakeAudioCapture: true,
    useFilesForFakeAudioCapture: process.env.DPE_CAMERA_FAKE_AUDIO,
    offloadAdmControls: true,
    asyncVideoInputDeviceInit: process.env.DPE_CAMERA_ASYNC_INIT === "1",
  });
  result.load_and_initialize_ms = performance.now() - start;
  let devices = [];
  for (let i = 0; i < 30 && !devices.length; i++) {
    await new Promise((resolve) => setTimeout(resolve, 200));
    devices = await new Promise((resolve) =>
      voice.getVideoInputDevices(resolve),
    );
  }
  const matches = devices.filter(
    (device) => device.name === process.env.DPE_CAMERA_DEVICE,
  );
  if (matches.length !== 1)
    throw Error("Expected exactly one camera with the specified name");
  selected = matches[0].guid;
  // Probe the native engine's control queue and fake-audio delivery while the
  // physical camera initializes. Loopback only; no Discord service or real mic.
  const connection = new voice.VoiceConnection("1234", {
    ssrc: 42, address: "127.0.0.1", port: Number(process.env.DPE_CAMERA_UDP_PORT), streamParameters: [],
  }, () => {});
  connection.setTransportOptions({encryptionSettings:{mode:'aead_xchacha20_poly1305_rtpsize',secretKey:Array(32).fill(1)}});
  connection.setSelfMute(false);
  connection.setSelfDeafen(false);
  connection.setPTTActive(true, false, false);
  const controlTimes = [], audioTimes = [];
  let pending = false;
  const heartbeat = setInterval(() => {
    if (pending) return;
    pending = true;
    voice.getInputDevices(() => { controlTimes.push(performance.now()); pending = false; });
  }, 25);
  voice.startRecordingRawSamples((_samples, _channels, rate) => {
    if (rate === 8000) audioTimes.push(performance.now());
  });
  await new Promise(resolve => setTimeout(resolve, 500));
  const began = performance.now();
  voice.setVideoInputDevice(selected);
  voice.setImageDataAllocator(
    (width, height) => new window.ImageData(width, height),
  );
  await new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(Error("First frame timed out")),
      18000,
    );
    voice.setVideoOutputSink(
      selected,
      (frame) => {
        // Inspect dimensions and timing only: never serialize, display or retain pixels.
        result.first_frame_ms = performance.now() - began;
        result.width = frame.width;
        result.height = frame.height;
        clearTimeout(timer);
        resolve();
      },
      true,
    );
    voice.signalVideoOutputSinkReady(selected);
  });
  await new Promise(resolve => setTimeout(resolve, 500));
  clearInterval(heartbeat);
  voice.stopRecordingRawSamples();
  connection.destroy();
  const maxGap = times => times.length > 1 ? Math.max(...times.slice(1).map((t, i) => t - times[i])) : null;
  result.engine_heartbeat_count = controlTimes.length;
  result.engine_max_gap_ms = maxGap(controlTimes);
  result.fake_audio_callbacks = audioTimes.length;
  result.fake_audio_max_gap_ms = maxGap(audioTimes);
  result.passed = true;
})()
  .catch((error) => {
    result = { ...result, passed: false, error: String(error) };
  })
  .finally(() => {
    if (voice && selected) voice.setVideoOutputSink(selected);
    ipcRenderer.send("camera-result", result);
  });
