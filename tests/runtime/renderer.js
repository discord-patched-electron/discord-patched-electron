const addon = window.fixtureAPI;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const assert = (value, message) => {
  if (!value) throw new Error(message);
};
const result = { passed: false };
const progress = (step) => addon.progress(step);
let interval;

async function run() {
  progress("renderer-started");
  progress("bridge-installed");
  const descriptor = Object.getOwnPropertyDescriptor(window, "createDiscordStream");
  assert(typeof descriptor?.value === "function", "Native stream function was not installed");
  assert(!descriptor.writable && !descriptor.configurable, "Unexpected stream function descriptor");
  const stream = window.createDiscordStream("fixture-video");
  const tracks = stream.getVideoTracks();
  assert(stream instanceof MediaStream && tracks.length === 1, "Expected one native video track");
  assert(tracks[0].readyState === "live", "Native video track is not live");
  progress("track-created");
  const video = document.getElementById("video");
  video.srcObject = stream;
  interval = setInterval(() => addon.push("fixture-video"), 33);
  await video.play();
  progress("video-playing");
  await new Promise((resolve) => video.requestVideoFrameCallback(resolve));
  const canvas = document.getElementById("canvas");
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.drawImage(video, 0, 0, 64, 48);
  const pixel = [...context.getImageData(16, 16, 1, 1).data];
  assert(video.videoWidth === 64 && video.videoHeight === 48, "Wrong native frame dimensions");
  const dimensions = [video.videoWidth, video.videoHeight];
  assert(
    pixel[0] > 180 && pixel[1] < 90 && pixel[2] < 90 && pixel[3] === 255,
    "Synthetic red frame was not delivered",
  );
  await delay(250);
  clearInterval(interval);
  interval = null;
  tracks[0].stop();
  video.srcObject = null;
  video.load();
  context.clearRect(0, 0, 64, 48);
  assert(tracks[0].readyState === "ended", "Native video track did not stop");
  await delay(250);
  addon.collect();
  for (let attempt = 0; attempt < 40; attempt++) {
    const counts = addon.counts();
    if (counts.sent === counts.released) break;
    await delay(50);
  }
  const before = addon.counts();
  assert(before.sent === before.released, "Native frames remained owned after track teardown");
  addon.push("fixture-does-not-exist");
  const after = addon.counts();
  assert(after.released === before.released + 1, "Missing-stream release callback did not run");
  assert(before.released > 0, "Delivered frame release callbacks did not run");
  addon.push("fixture-video");
  const stopped = addon.counts();
  assert(stopped.released === after.released + 1, "Stopped stream retained its native source");
  result.bridge = {
    dimensions,
    pixel,
    track_ended: true,
    counts: stopped,
    missing_stream_released: true,
    stopped_stream_released: true,
  };

  Object.assign(result, await addon.loadModules());
  if (result.audio_batches) {
    assert(result.audio_batches.errors.length === 0, "Voice processing reported errors");
    assert(
      result.audio_batches.batches.every((batch) => batch.failed === 0 && batch.processed > 0),
      "Audio batch failed",
    );
  }
  result.passed = true;
}
run()
  .catch((error) => {
    result.error = error.stack;
  })
  .finally(() => {
    if (interval) clearInterval(interval);
    addon.report(result);
  });
