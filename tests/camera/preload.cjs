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
    offloadAdmControls: true,
    asyncVideoInputDeviceInit: false,
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
  result.passed = true;
})()
  .catch((error) => {
    result = { ...result, passed: false, error: String(error) };
  })
  .finally(() => {
    if (voice && selected) voice.setVideoOutputSink(selected);
    ipcRenderer.send("camera-result", result);
  });
