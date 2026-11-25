const { app } = require("electron");
const { createInterface } = require("node:readline");
const createWindow = require("./window.cjs");
const describeFailure = require("./failure.cjs");
const preferences = require("./profile.cjs");

app.setPath("userData", process.env.DPE_SPLASH_USER_DATA);
app.setDesktopName(process.env.CHROME_DESKTOP);
if (preferences.settings(process.env.DPE_PROFILE_DIR).enableHardwareAcceleration === false)
  app.disableHardwareAcceleration();

let shown = false;
let state = { stage: "download", label: "Discord Patched Electron" };
let view;
app.on("window-all-closed", () => app.exit(shown ? 42 : 2));
const input = createInterface({ input: process.stdin });
input.on("line", (line) => {
  const message = JSON.parse(line);
  if (message.stage === "install") state = { ...state, stage: "install" };
  else if (message.stage === "error")
    state = { stage: "error", ...describeFailure(message.details), seconds: null };
  else return;
  view?.update(state);
});
input.once("close", () => app.exit(0));
app
  .whenReady()
  .then(() => {
    view = createWindow((action) => {
      if (state.stage === "error" && action === "retry") app.exit(43);
    });
    view.update(state);
    view.window.webContents.once("render-process-gone", () => app.exit(2));
    view.window.once("ready-to-show", () => {
      shown = true;
      process.stdout.write("ready\n");
    });
  })
  .catch((error) => {
    console.error(error);
    app.exit(2);
  });
