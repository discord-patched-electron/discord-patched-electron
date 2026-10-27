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
  video.src = new URLSearchParams(location.search).get("clip") || "clip.mp4";
  await video.play().catch((error) => {
    // DOMException loses its message across executeJavaScript's IPC boundary.
    throw new Error(`${error.name}: ${error.message}; ${video.error?.message || "no media error"}`);
  });
  await delivered;
  // Chromium batches Media-domain diagnostics; keep playback alive long enough
  // to report the actual decoder, rather than infer it from GPU capabilities.
  await new Promise((resolve) => setTimeout(resolve, 1500));
  video.pause();
  const encoders = [];
  const decoders = [];
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
        chunks = [],
        decoderConfig,
        failure;
      try {
        const support = await VideoEncoder.isConfigSupported(config);
        if (!support.supported) {
          encoders.push({ codec, preference, supported: false });
          continue;
        }
        encoder = new VideoEncoder({
          output: (chunk, metadata) => {
            chunks.push(chunk);
            if (metadata.decoderConfig) decoderConfig = metadata.decoderConfig;
          },
          error: (error) => {
            failure = String(error);
          },
        });
        encoder.configure(config);
        for (let i = 0; i < 12; i++) {
          // Neutral levels avoid conflating BT.601/BT.709 color conversion with
          // codec delivery. The ramp still catches blank, stale or corrupt frames.
          ctx.fillStyle = `rgb(${i * 20},${i * 20},${i * 20})`;
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
          chunks: chunks.length,
          ...(failure ? { error: failure } : {}),
        });
        if (failure || chunks.length !== 12) continue;
        // Decode the actual encoded bytes, checking every frame's timestamp,
        // dimensions and pixels. Capability queries alone don't prove playback.
        for (const decodePreference of ["no-preference", "prefer-hardware", "prefer-software"]) {
          const decoding = {
            ...(decoderConfig || { codec, codedWidth: 1280, codedHeight: 720 }),
            hardwareAcceleration: decodePreference,
          };
          const result = {
            codec,
            encodedWith: preference,
            preference: decodePreference,
            required: decodePreference === "no-preference",
          };
          let decoder, decodeFailure;
          const decoded = [];
          const sample = document.createElement("canvas");
          sample.width = sample.height = 1;
          const sampleCtx = sample.getContext("2d", { willReadFrequently: true });
          try {
            const support = await VideoDecoder.isConfigSupported(decoding);
            if (!support.supported) {
              decoders.push({ ...result, supported: false });
              continue;
            }
            decoder = new VideoDecoder({
              output: (frame) => {
                try {
                  sampleCtx.drawImage(frame, 0, 0, 1, 1);
                  const pixel = [...sampleCtx.getImageData(0, 0, 1, 1).data];
                  const level = (frame.timestamp / 33333) * 20;
                  const expected = [level, level, level, 255];
                  decoded.push({
                    timestamp: frame.timestamp,
                    width: frame.displayWidth,
                    height: frame.displayHeight,
                    pixel,
                    passed:
                      frame.displayWidth === 1280 &&
                      frame.displayHeight === 720 &&
                      pixel.every((value, i) => Math.abs(value - expected[i]) <= 12),
                  });
                } catch (error) {
                  decodeFailure = String(error);
                } finally {
                  frame.close();
                }
              },
              error: (error) => {
                decodeFailure = String(error);
              },
            });
            decoder.configure(decoding);
            for (const chunk of chunks) decoder.decode(chunk);
            await decoder.flush();
            const timestamps = decoded.map((frame) => frame.timestamp).sort((a, b) => a - b);
            decoders.push({
              ...result,
              supported: true,
              frames: decoded.length,
              decoded,
              passed:
                !decodeFailure &&
                decoded.length === 12 &&
                decoded.every((frame) => frame.passed) &&
                timestamps.every((timestamp, i) => timestamp === i * 33333),
              ...(decodeFailure ? { error: decodeFailure } : {}),
            });
          } catch (error) {
            decoders.push({
              ...result,
              passed: false,
              frames: decoded.length,
              error: String(error),
            });
          } finally {
            if (decoder && decoder.state !== "closed") decoder.close();
          }
        }
      } catch (error) {
        encoders.push({ codec, preference, chunks: chunks.length, error: String(error) });
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
    decoders,
    webglPassed: pixel.every((value, i) => Math.abs(value - [26, 128, 230, 255][i]) <= 1),
  };
})();
