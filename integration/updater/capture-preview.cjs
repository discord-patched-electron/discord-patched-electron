"use strict";
const { spawn } = require("node:child_process");

// Thumbnail pixels are transient IPC data. Never write them to the profile,
// diagnostic log or filesystem. Both producers downscale on the GPU first.
function jpeg(value) {
  const { width, height } = value || {};
  if (
    !Number.isInteger(width) ||
    !Number.isInteger(height) ||
    width < 1 ||
    height < 1 ||
    width > 480 ||
    height > 270
  )
    return {};
  let pixels = value.pixels;
  if (typeof pixels === "string" && pixels.length <= (480 * 270 * 4 * 4) / 3 + 4)
    pixels = Buffer.from(pixels, "base64");
  if (!Buffer.isBuffer(pixels) || pixels.length !== width * height * 4 || value.format !== "BGRA")
    return {};
  const image = require("electron").nativeImage.createFromBitmap(pixels, {
    width,
    height,
    scaleFactor: 1,
  });
  if (image.isEmpty()) return {};
  const encoded = image.toJPEG(65);
  return encoded.length <= 140000
    ? { url: `data:image/jpeg;base64,${encoded.toString("base64")}` }
    : {};
}
// One native session per AbortSignal, one outstanding demand, no pixel queue.
// Helpers retain GPU/import state but never capture ahead of consumer demand.
const screens = new WeakMap();
function screen(file, args, env, signal) {
  if (signal.aborted) return Promise.resolve({});
  let session = screens.get(signal);
  if (!session) {
    const child = spawn(file, [...args, "--persistent", "1"], {
      env,
      stdio: ["pipe", "pipe", "ignore"],
    });
    let buffer = Buffer.alloc(0),
      receive,
      timer,
      ended = false;
    const finish = (value) => {
      clearTimeout(timer);
      const callback = receive;
      receive = undefined;
      callback?.(value);
    };
    const close = () => {
      if (ended) return;
      ended = true;
      signal.removeEventListener("abort", close);
      child.kill("SIGKILL");
      buffer = Buffer.alloc(0);
      finish({});
    };
    signal.addEventListener("abort", close, { once: true });
    child.on("error", close);
    child.on("exit", close);
    child.stdin.on("error", close);
    child.stdout.on("data", (data) => {
      if (!receive || buffer.length + data.length > 480 * 270 * 4 + 80) return close();
      buffer = Buffer.concat([buffer, data]);
      const end = buffer.indexOf(10);
      if (end < 0) {
        if (buffer.length > 80) close();
        return;
      }
      const match =
        end < 80 && /^DPE-PREVIEW (\d+) (\d+)$/.exec(buffer.subarray(0, end).toString("ascii"));
      if (!match) return close();
      const width = Number(match[1]),
        height = Number(match[2]);
      if (width < 1 || height < 1 || width > 480 || height > 270) return close();
      const bytes = width * height * 4;
      if (buffer.length < end + 1 + bytes) return;
      if (buffer.length !== end + 1 + bytes) return close();
      const pixels = buffer.subarray(end + 1);
      buffer = Buffer.alloc(0);
      finish({ width, height, format: "BGRA", pixels });
    });
    let first = true;
    session = {
      next() {
        if (ended || receive) return Promise.resolve({});
        return new Promise((resolve) => {
          receive = resolve;
          timer = setTimeout(close, 3500);
          if (!first) child.stdin.write("\n");
          first = false;
        });
      },
    };
    screens.set(signal, session);
  }
  return session.next();
}
module.exports = { jpeg, screen };
