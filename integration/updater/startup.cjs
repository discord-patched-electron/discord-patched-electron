const { app } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const createWindow = require("./window.cjs");
const runUpdate = require("./runner.cjs");
const preferences = require("./profile.cjs");

const root = process.env.DPE_APP_ROOT;
const profile = process.env.DPE_PROFILE_DIR;
fs.mkdirSync(profile, { recursive: true, mode: 0o700 });
app.setPath("userData", profile);
if (preferences.settings(profile).enableHardwareAcceleration === false)
  app.disableHardwareAcceleration();
let view,
  task,
  retry,
  complete = false;
const cancel = () => {
  clearTimeout(retry);
  task?.cancel();
};
app.on("before-quit", cancel);
for (const signal of ["SIGINT", "SIGTERM"])
  process.once(signal, () => {
    cancel();
    app.exit(1);
  });
if (
  !app.requestSingleInstanceLock() &&
  !preferences.config(profile).multiInstance &&
  !process.argv.includes("--multi-instance")
)
  app.exit(1);
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
        if (task) return;
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
          await task.promise;
          complete = true;
          app.exit(0);
        } catch (error) {
          console.error(error.message);
          if (!view.window.isDestroyed()) {
            view.update({ stage: quickstart ? "check" : "error" });
            retry = setTimeout(() => void attempt(), quickstart ? 0 : 10000);
            quickstart = false;
          }
        } finally {
          task = null;
        }
      };
      view = createWindow(() => view.window.close());
      view.window.once("closed", () => {
        cancel();
        if (!complete) app.exit(1);
      });
      // Do not hide the window behind network or dependency work on first launch.
      view.window.webContents.once("did-finish-load", () => void attempt());
    })
    .catch((error) => {
      console.error(error);
      app.exit(1);
    });
}
