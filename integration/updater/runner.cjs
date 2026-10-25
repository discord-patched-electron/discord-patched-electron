const { spawn } = require("node:child_process");
const path = require("node:path");

module.exports = function runUpdate(
  {
    root,
    clientRoot,
    channel,
    offline = process.env.DPE_CLIENT_OFFLINE === "1",
    prepareOnly = false,
    timeoutMs = 15 * 60 * 1000,
    killDelayMs = 1000,
    onProgress = () => {},
  },
  launch = spawn,
) {
  const child = launch(
    "python3",
    [path.join(root, "integration/updater/check.py"), root, clientRoot, channel],
    {
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
      env: {
        ...process.env,
        LD_PRELOAD: "",
        PYTHONHOME: "",
        PYTHONPATH: path.join(root, "python"),
        PYTHONNOUSERSITE: "1",
        PYTHONDONTWRITEBYTECODE: "1",
        DPE_UPDATE_PROGRESS: "1",
        DPE_CLIENT_OFFLINE: offline ? "1" : "0",
        DPE_PREPARE_ONLY: prepareOnly ? "1" : "0",
      },
    },
  );
  let cancelled = false,
    finished = false,
    killTimer;
  const signal = (name) => {
    try {
      if (Number.isInteger(child.pid) && child.pid > 0) process.kill(-child.pid, name);
      else child.kill(name);
    } catch (error) {
      if (error.code !== "ESRCH") child.kill(name);
    }
  };
  const cancel = () => {
    if (finished || cancelled) return;
    cancelled = true;
    killTimer = setTimeout(() => signal("SIGKILL"), killDelayMs);
    signal("SIGTERM");
  };
  const promise = new Promise((resolve, reject) => {
    let buffer = "",
      diagnostics = "",
      result,
      failure;
    const timer = setTimeout(() => {
      failure = new Error("The update took too long. Check your connection and try again.");
      cancel();
    }, timeoutMs);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", (data) => {
      diagnostics = (diagnostics + data).slice(-16000);
      process.stderr.write(data);
    });
    child.stdout.on("data", (data) => {
      buffer += data;
      if (buffer.length > 65536) {
        failure = new Error("The updater returned an oversized response.");
        cancel();
        return;
      }
      let newline;
      while ((newline = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        if (!line.trim()) continue;
        try {
          const value = JSON.parse(line);
          if (value.type === "progress") onProgress(value);
          else if (
            (prepareOnly
              ? ["host", "runtime"]
              : ["host", "runtime", "installed_runtime", "voice"]
            ).every(
              (key) =>
                typeof value[key] === "string" &&
                path.isAbsolute(value[key]) &&
                !/[\r\n]/.test(value[key]),
            )
          )
            result = value;
          else throw new Error("The updater returned an invalid result.");
        } catch (error) {
          failure = error;
          cancel();
        }
      }
    });
    child.once("error", (error) => {
      finished = true;
      clearTimeout(timer);
      clearTimeout(killTimer);
      reject(
        new Error(
          error.code === "ENOENT"
            ? "Python 3 is missing. Install it with your package manager; the updater will retry."
            : error.message,
        ),
      );
    });
    child.once("close", (code) => {
      finished = true;
      clearTimeout(timer);
      clearTimeout(killTimer);
      if (failure) reject(failure);
      else if (cancelled) reject(new Error("Update cancelled."));
      else if (code !== 0)
        reject(
          new Error(
            diagnostics.trim() || "The update failed. Check your connection and try again.",
          ),
        );
      else if (!result || buffer.trim())
        reject(new Error("The updater did not finish preparing Discord."));
      else resolve(result);
    });
  });
  return { promise, cancel };
};
