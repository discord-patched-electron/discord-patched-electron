// Exercise Discord's main-process capture lifetime using synthetic pages only.
const { app, BrowserWindow, session } = require("electron");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const v8 = require("node:v8");
const vm = require("node:vm");

// app.commandLine's js-flags affects subsequently created renderer isolates;
// the main-process isolate already exists when this fixture starts.
v8.setFlagsFromString("--expose-gc");
const forceGC = vm.runInNewContext("gc");

const output = process.env.DPE_FRAME_CAPTURE_OUTPUT;
if (!output) throw new Error("An isolated output directory is required");
app.setPath("userData", path.join(output, "profile"));
app.commandLine.appendSwitch("js-flags", "--expose-gc");
app.commandLine.appendSwitch("disable-background-networking");
app.commandLine.appendSwitch("disable-renderer-backgrounding");
const results = [];
let finished = false;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function finish(error) {
  if (finished) return;
  finished = true;
  fs.writeFileSync(
    path.join(output, "frame-capture.json"),
    JSON.stringify(
      {
        schema: 1,
        fixture: "frame-capture-lifetime",
        versions: process.versions,
        passed: !error,
        cases: results,
        error: error?.stack,
        synthetic_content_only: true,
        frame_pixels_saved: false,
        native_sink_delivery_verified: false,
      },
      null,
      2,
    ) + "\n",
  );
  for (const window of BrowserWindow.getAllWindows()) window.destroy();
  app.exit(error ? 1 : 0);
}

async function collect() {
  assert.equal(typeof forceGC, "function");
  for (let i = 0; i < 3; i++) {
    await delay(20);
    forceGC();
  }
}

async function windowWithPage(label) {
  const window = new BrowserWindow({
    width: 240,
    height: 180,
    show: true,
    webPreferences: { sandbox: true, contextIsolation: true, backgroundThrottling: false },
  });
  window.webContents.on("render-process-gone", (_event, details) =>
    finish(new Error("Synthetic renderer exited: " + JSON.stringify(details))),
  );
  await window.loadURL(
    "data:text/html," +
      encodeURIComponent(
        `<title>Capture lifecycle test</title><body style="background:#345;color:white">${label}</body>`,
      ),
  );
  return window;
}

async function exercise(mode) {
  const target = await windowWithPage("Synthetic capture source");
  const consumer = await windowWithPage("Synthetic capture consumer");
  assert.throws(
    () =>
      target.webContents.mainFrame.captureStream({
        consumerFrame: target.webContents.mainFrame,
      }),
    /cannot consume its own capture/,
  );

  let capture = target.webContents.mainFrame.captureStream({
    consumerFrame: consumer.webContents.mainFrame,
    captureId: "dpe-lifetime-" + mode,
    audio: false,
    maxFrameRate: 10,
  });
  assert.equal(capture.captureId, "dpe-lifetime-" + mode);
  let stopCount = 0;
  let resolveStopped;
  const stopped = new Promise((resolve) => {
    resolveStopped = resolve;
  });
  capture.on("stopped", (_event, reason) => {
    stopCount++;
    resolveStopped(reason);
  });
  const started = new Promise((resolve) =>
    capture.once("started", (_event, details) => resolve(details)),
  );
  let details;
  if (mode === "immediate-stop") {
    capture.stop();
    capture.stop();
    assert.equal(capture.capturing, false);
    // The queued stopped event must survive loss of the last JS reference.
    capture = null;
    forceGC();
  } else {
    details = await Promise.race([
      started,
      stopped.then((reason) => {
        throw new Error("Stopped before starting: " + reason);
      }),
    ]);
    assert.equal(details.captureId, "dpe-lifetime-" + mode);
    assert.equal(details.targetKind, "main-frame");
    assert.equal(capture.capturing, true);
    capture.setConstraints({ maxFrameRate: 5, maxSize: { width: 160, height: 120 }, audio: true });
    capture.setConstraints({ audio: false });
    if (mode === "explicit-stop") {
      capture.stop();
      capture.stop();
      assert.equal(capture.capturing, false);
    } else {
      capture = null;
      await collect();
      assert.equal(stopCount, 0, "Active capture must survive JS garbage collection");
      if (mode === "target-destroyed") target.destroy();
      else consumer.destroy();
    }
  }
  const reason = await stopped;
  assert.equal(
    reason,
    mode === "target-destroyed"
      ? "frame-destroyed"
      : mode === "consumer-destroyed"
        ? "consumer-gone"
        : "stopped",
  );
  capture = null;
  await collect();
  assert.equal(stopCount, 1, "Stopped must be emitted exactly once");
  results.push({ mode, passed: true, reason, stopCount, details });
  if (!target.isDestroyed()) target.destroy();
  if (!consumer.isDestroyed()) consumer.destroy();
}

app.on("window-all-closed", () => {});
app
  .whenReady()
  .then(async () => {
    session.defaultSession.webRequest.onBeforeRequest((details, callback) =>
      callback({ cancel: !details.url.startsWith("data:") }),
    );
    session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) =>
      callback(false),
    );
    for (const mode of [
      "explicit-stop",
      "immediate-stop",
      "target-destroyed",
      "consumer-destroyed",
    ]) {
      await exercise(mode);
    }
    finish();
  })
  .catch(finish);
setTimeout(() => finish(new Error("Capture lifecycle fixture timed out")), 30000);
