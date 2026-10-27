const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { test } = require("node:test");
const confirmApplication = require("../../integration/updater/application-ready.cjs");

test("startup stays unconfirmed until the correct Discord window loads", () => {
  const app = new EventEmitter();
  const writes = [];
  confirmApplication(app, "/profile/trial-ready", "canary", (...args) => writes.push(args));
  app.emit("ready");
  assert.equal(writes.length, 0);
  function load(url) {
    const webContents = Object.assign(new EventEmitter(), { getURL: () => url });
    app.emit("browser-window-created", {}, { webContents });
    assert.equal(writes.length, 0);
    webContents.emit("did-finish-load");
    return webContents;
  }
  load("dpe-splash://app/index.html");
  load("file:///application/openasar/config.html");
  load("https://discord.com/login");
  load("http://canary.discord.com/login");
  load("https://canary.discord.com.example.com/login");
  const main = load("https://canary.discord.com/login");
  assert.deepEqual(writes, [["/profile/trial-ready", "ready\n", { mode: 0o600 }]]);
  main.emit("did-finish-load");
  assert.equal(writes.length, 1);
});

test("a crashed or closed window never confirms the trial", () => {
  const app = new EventEmitter();
  const writes = [];
  confirmApplication(app, "/profile/trial-ready", "canary", (...args) => writes.push(args));
  const webContents = new EventEmitter();
  app.emit("browser-window-created", {}, { webContents });
  webContents.emit("render-process-gone", {}, { reason: "crashed" });
  webContents.emit("destroyed");
  app.emit("quit");
  assert.equal(writes.length, 0);
});
