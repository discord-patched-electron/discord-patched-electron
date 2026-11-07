// Bounded local diagnostics. Export applies privacy redaction before sharing.
const fs = require("node:fs");
const path = require("node:path");
const LIMIT = 4 * 1024 * 1024;
let installed = false;
function record(event, details, profile = process.env.DPE_PROFILE_DIR) {
  if (!profile) return;
  try {
    const directory = path.join(profile, "logs/dpe");
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    const file = path.join(directory, "runtime.log");
    if (fs.lstatSync(file, { throwIfNoEntry: false })?.size >= LIMIT) {
      fs.rmSync(file + ".2", { force: true });
      if (fs.existsSync(file + ".1")) fs.renameSync(file + ".1", file + ".2");
      fs.renameSync(file, file + ".1");
    }
    const line = JSON.stringify({ time: new Date().toISOString(), event, details });
    const fd = fs.openSync(
      file,
      fs.constants.O_WRONLY |
        fs.constants.O_APPEND |
        fs.constants.O_CREAT |
        fs.constants.O_NOFOLLOW |
        fs.constants.O_NONBLOCK,
      0o600,
    );
    try {
      if (!fs.fstatSync(fd).isFile()) return;
      fs.writeSync(
        fd,
        line.length <= 65536
          ? line + "\n"
          : JSON.stringify({ event, details: "Oversized diagnostic omitted" }) + "\n",
      );
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    /* Logging must never change application behavior. */
  }
}
function lineSink(event, details) {
  let pending = "",
    oversized = false;
  const { StringDecoder } = require("node:string_decoder");
  const decoder = new StringDecoder("utf8");
  const emit = () =>
    record(event, {
      ...details,
      message: oversized ? "Oversized log line omitted" : pending.trimEnd(),
    });
  return {
    write(chunk) {
      const text = Buffer.isBuffer(chunk) ? decoder.write(chunk) : String(chunk);
      for (const part of text.match(/[^\n]*\n|[^\n]+$/g) || []) {
        if (!oversized && pending.length + part.length <= 65536) pending += part;
        else {
          pending = "";
          oversized = true;
        }
        if (part.endsWith("\n")) {
          emit();
          pending = "";
          oversized = false;
        }
      }
    },
    end() {
      pending += decoder.end();
      if (pending || oversized) emit();
      pending = "";
      oversized = false;
    },
  };
}
function capture(stream, event, details = {}) {
  if (!stream?.on) return;
  const sink = lineSink(event, details);
  stream.on("data", (chunk) => sink.write(chunk));
  stream.once("end", () => sink.end());
}
function install(app) {
  if (installed || !process.env.DPE_PROFILE_DIR) return;
  installed = true;
  record("process-start", { pid: process.pid, versions: process.versions });
  for (const name of process.env.DPE_DIAGNOSTIC_RELAY === "1" ? [] : ["stdout", "stderr"]) {
    const stream = process[name];
    const write = stream.write;
    const sink = lineSink(name, {});
    stream.write = function (chunk, ...args) {
      try {
        sink.write(chunk);
      } catch {
        /* Preserve original stream semantics. */
      }
      return Reflect.apply(write, this, [chunk, ...args]);
    };
    process.once("exit", () => sink.end());
  }
  process.on("uncaughtExceptionMonitor", (error, origin) =>
    record("uncaught-exception", { origin, stack: error?.stack }),
  );
  process.once("exit", (code) => record("process-exit", { code }));
  const attach = (contents) => {
    const allowed = () => {
      try {
        const url = new URL(contents.getURL());
        return (
          (url.protocol === "https:" &&
            ["discord.com", "canary.discord.com", "ptb.discord.com"].includes(url.hostname)) ||
          (url.protocol === "dpe-splash:" && ["app", "welcome", "startup"].includes(url.hostname))
        );
      } catch {
        return false;
      }
    };
    contents.on("console-message", (event) => {
      if (allowed())
        record("renderer-console", {
          renderer: contents.id,
          level: event.level,
          message:
            String(event.message || "").length <= 16000
              ? String(event.message || "")
              : "Oversized console message omitted",
          line: event.lineNumber,
        });
    });
    contents.on("render-process-gone", (_event, details) =>
      record("renderer-gone", { renderer: contents.id, ...details }),
    );
    contents.on("unresponsive", () => record("renderer-unresponsive", { renderer: contents.id }));
    contents.on("did-fail-load", (_event, code, description, _url, mainFrame) => {
      if (mainFrame && allowed())
        record("renderer-load-failed", { renderer: contents.id, code, description });
    });
  };
  app.on("web-contents-created", (_event, contents) => attach(contents));
  app.on("child-process-gone", (_event, details) => record("child-process-gone", details));
  const timer = setInterval(() => {
    if (!app.isReady?.()) return;
    void Promise.resolve()
      .then(() => require("./screensharing.cjs").snapshot())
      .then((state) => {
        if (state.active === true || state.watching?.length) record("media-statistics", state);
      })
      .catch(() => {});
  }, 15000);
  timer.unref();
  app.once("will-quit", () => clearInterval(timer));
}
module.exports = { record, install, capture };
