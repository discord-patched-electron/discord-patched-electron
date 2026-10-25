const { app } = require("electron");
const fs = require("node:fs");
const path = require("node:path");
const createWindow = require("./window.cjs");
const runUpdate = require("./runner.cjs");

const root = process.env.DPE_APP_ROOT;
const profile = path.join(process.env.DISCORD_USER_DATA_DIR, "dpe-updater/ui");
fs.mkdirSync(profile, { recursive: true, mode: 0o700 });
app.setPath("userData", profile);
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
if (!app.requestSingleInstanceLock()) app.exit(1);
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
      const attempt = async () => {
        if (task) return;
        clearTimeout(retry);
        view.update({ stage: "check", label: "Checking for updates" });
        task = runUpdate({
          root,
          clientRoot: process.env.DPE_CLIENT_ROOT,
          channel,
          onProgress: (state) => view.update(state),
        });
        try {
          await task.promise;
          complete = true;
          app.exit(0);
        } catch (error) {
          console.error(error.message);
          if (!view.window.isDestroyed()) {
            view.update({ stage: "error" });
            retry = setTimeout(() => void attempt(), 10000);
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
