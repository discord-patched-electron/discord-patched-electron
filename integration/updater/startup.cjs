const { app } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const createWindow = require("./window.cjs");
const runUpdate = require("./runner.cjs");
const preferences = require("./profile.cjs");
const describeFailure = require("./failure.cjs");

const root = process.env.DPE_APP_ROOT;
const profile = process.env.DPE_PROFILE_DIR;
fs.mkdirSync(profile, { recursive: true, mode: 0o700 });
app.setPath("userData", profile);
if (preferences.settings(profile).enableHardwareAcceleration === false)
  app.disableHardwareAcceleration();
let view,
  task,
  retry,
  failed,
  closing = false,
  complete = false;
const cancel = () => {
  closing = true;
  clearTimeout(retry);
  task?.cancel();
};
const shutdown = async () => {
  cancel();
  try {
    await task?.promise;
  } catch {
    // Cancellation is expected. Wait for the helper group before exiting.
  }
  app.exit(complete ? 0 : 1);
};
app.on("before-quit", (event) => {
  if (complete) return;
  event.preventDefault();
  void shutdown();
});
for (const signal of ["SIGINT", "SIGTERM"])
  process.once(signal, () => {
    void shutdown();
  });
if (
  !app.requestSingleInstanceLock() &&
  !preferences.config(profile).multiInstance &&
  !process.argv.includes("--multi-instance")
)
  app.exit(2);
else {
  app.on("second-instance", () => {
    view?.window.show();
    view?.window.focus();
  });
  app
    .whenReady()
    .then(() => {
      const channel = JSON.parse(
        fs.readFileSync(path.join(root, "resources/build_info.json"), "utf8"),
      ).releaseChannel;
      let quickstart = Boolean(
        (preferences.config(profile).quickstart || process.env.OPENASAR_QUICKSTART) &&
        fs.existsSync(path.join(process.env.DPE_CLIENT_ROOT, "current.json")),
      );
      const attempt = async () => {
        if (task || closing) return;
        failed = null;
        clearTimeout(retry);
        view.update({ stage: "check", label: "Checking for updates" });
        task = runUpdate({
          root,
          clientRoot: process.env.DPE_CLIENT_ROOT,
          channel,
          offline: quickstart || process.env.DPE_CLIENT_OFFLINE === "1",
          onProgress: (state) => view.update(state),
        });
        try {
          const prepared = await task.promise;
          if (closing) return;
          if (process.env.DPE_LAUNCH_RESULT)
            fs.writeFileSync(process.env.DPE_LAUNCH_RESULT, JSON.stringify(prepared), {
              mode: 0o600,
            });
          complete = true;
          app.exit(0);
        } catch (error) {
          console.error(error.message);
          if (!closing && !view.window.isDestroyed()) {
            failed = describeFailure(error);
            view.update(quickstart ? { stage: "check" } : { stage: "error", ...failed });
            if (quickstart || failed.seconds !== null)
              retry = setTimeout(() => void attempt(), quickstart ? 0 : failed.seconds * 1000);
            quickstart = false;
          }
        } finally {
          task = null;
        }
      };
      view = createWindow((action) => {
        if (action === "close") view.window.close();
        else if (action === "retry" && failed) void attempt();
        else if (action === "pause" && failed) {
          clearTimeout(retry);
          failed.seconds = null;
          view.update({ stage: "error", ...failed });
        }
      });
      view.window.once("closed", () => {
        if (!complete) void shutdown();
      });
      // Do not hide the window behind network or dependency work on first launch.
      // Fonts and animation are optional; a slow CDN must not delay updates.
      view.window.webContents.once("dom-ready", () => void attempt());
    })
    .catch((error) => {
      console.error(error);
      app.exit(1);
    });
}
