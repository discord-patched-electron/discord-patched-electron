(async () => {
  const canvas = document.querySelector("canvas");
  const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
  if (!gl) throw new Error("No WebGL context");
  gl.clearColor(0.1, 0.5, 0.9, 1);
  gl.clear(gl.COLOR_BUFFER_BIT);
  const pixel = new Uint8Array(4);
  gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
  const info = gl.getExtension("WEBGL_debug_renderer_info");
  const renderer = info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
  const video = document.querySelector("video");
  let frames = 0;
  const delivered = new Promise((resolve, reject) => {
    video.addEventListener("error", () => reject(new Error(video.error?.message || "Video failed")), {once: true});
    function frame() {
      if (++frames >= 5) resolve();
      else video.requestVideoFrameCallback(frame);
    }
    video.requestVideoFrameCallback(frame);
  });
  video.src = "clip.mp4";
  await video.play();
  await delivered;
  // Chromium batches Media-domain diagnostics; keep playback alive long enough
  // to report the actual decoder, rather than infer it from GPU capabilities.
  await new Promise(resolve => setTimeout(resolve, 1500));
  video.pause();
  return {renderer, pixel: [...pixel], videoFrames: frames, videoWidth: video.videoWidth,
    videoHeight: video.videoHeight, webglPassed: pixel.every((value, i) => Math.abs(value - [26,128,230,255][i]) <= 1)};
})();
