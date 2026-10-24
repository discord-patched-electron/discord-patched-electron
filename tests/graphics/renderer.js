(async () => {
  const canvas = document.querySelector("canvas");
  const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
  if (!gl) throw new Error("No WebGL context");
  gl.clearColor(0.1, 0.5, 0.9, 1);
  gl.clear(gl.COLOR_BUFFER_BIT);
  const pixel = new Uint8Array(4);
  gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
  const info = gl.getExtension("WEBGL_debug_renderer_info");
  const renderer = info
    ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL)
    : gl.getParameter(gl.RENDERER);
  const video = document.querySelector("video");
  let frames = 0;
  const delivered = new Promise((resolve, reject) => {
    video.addEventListener(
      "error",
      () => reject(new Error(video.error?.message || "Video failed")),
      { once: true },
    );
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
  await new Promise((resolve) => setTimeout(resolve, 1500));
  video.pause();
  const encoders = [];
  const synthetic = document.createElement("canvas");
  synthetic.width = 1280;
  synthetic.height = 720;
  const ctx = synthetic.getContext("2d");
  for (const preference of ["prefer-hardware", "prefer-software"]) {
    for (const codec of ["avc1.42001f", "vp8", "vp09.00.10.08", "av01.0.04M.08"]) {
      const config = {
        codec,
        width: 1280,
        height: 720,
        bitrate: 2000000,
        framerate: 30,
        hardwareAcceleration: preference,
      };
      let encoder,
        chunks = 0,
        failure;
      try {
        const support = await VideoEncoder.isConfigSupported(config);
        if (!support.supported) {
          encoders.push({ codec, preference, supported: false });
          continue;
        }
        encoder = new VideoEncoder({
          output: () => chunks++,
          error: (error) => {
            failure = String(error);
          },
        });
        encoder.configure(config);
        for (let i = 0; i < 12; i++) {
          ctx.fillStyle = `rgb(${i * 20},100,180)`;
          ctx.fillRect(0, 0, 1280, 720);
          const frame = new VideoFrame(synthetic, { timestamp: i * 33333 });
          encoder.encode(frame, { keyFrame: i === 0 });
          frame.close();
        }
        await encoder.flush();
        encoders.push({
          codec,
          preference,
          supported: true,
          chunks,
          ...(failure ? { error: failure } : {}),
        });
      } catch (error) {
        encoders.push({ codec, preference, chunks, error: String(error) });
      } finally {
        if (encoder && encoder.state !== "closed") encoder.close();
      }
    }
  }
  return {
    renderer,
    pixel: [...pixel],
    videoFrames: frames,
    videoWidth: video.videoWidth,
    videoHeight: video.videoHeight,
    encoders,
    webglPassed: pixel.every((value, i) => Math.abs(value - [26, 128, 230, 255][i]) <= 1),
  };
})();
