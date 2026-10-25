const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

function fixture(env = {}, installedHost) {
  const calls = [];
  const app = Object.assign(new EventEmitter(), {
    relaunch: () => calls.push("relaunch"),
    quit: () => calls.push("quit"),
  });
  const module = { exports: {} };
  const pending = [];
  let tick;
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "../../integration/updater/module-updater.cjs"), "utf8"),
    {
      module,
      require: (name) =>
        name === "./runner.cjs"
          ? (options) => {
              let resolve, reject;
              const promise = new Promise((yes, no) => {
                resolve = yes;
                reject = no;
              });
              pending.push({
                options,
                async done(value, error) {
                  if (error) reject(error);
                  else resolve(value);
                },
              });
              return {
                promise,
                cancel: () => {
                  calls.push("cancel");
                  reject(new Error("cancelled"));
                },
              };
            }
          : require(name),
      process: { env, execPath: "/portable app/runtime/electron" },
      queueMicrotask,
      console: { error: () => {} },
      clearInterval: () => {},
      setInterval(callback, duration) {
        assert.equal(duration, 15 * 60 * 1000);
        tick = callback;
        return { unref() {} };
      },
    },
  );
  const updater = module.exports(
    app,
    {
      channel: "canary",
      modules: { discord_voice: { version: 7 } },
    },
    "/profile/generations/old",
    "/portable app",
    "/profile",
    installedHost,
  );
  const checked = [],
    downloaded = [];
  updater.events.on(updater.UPDATE_CHECK_FINISHED, (event) => checked.push(event));
  updater.events.on(updater.DOWNLOADING_MODULES_FINISHED, (event) => downloaded.push(event));
  return { updater, app, calls, pending, tick, checked, downloaded };
}

test("module requests expose only installed modules and preserve version checks", async () => {
  const { updater } = fixture();
  assert.equal(updater.isInstalled("discord_voice", 7), true);
  assert.equal(updater.isInstalled("discord_voice", 8), false);
  updater.getInstalled().discord_voice.installedVersion = 8;
  assert.equal(updater.isInstalled("discord_voice", 7), true);
  assert.equal(updater.isInstalled("toString"), false);
  assert.equal(updater.getModulePath("../discord_voice"), null);
  assert.equal(
    updater.getModulePath("discord_voice"),
    "/profile/generations/old/modules/discord_voice",
  );
  const installed = [];
  updater.events.on(updater.INSTALLED_MODULE, (event) => installed.push(event));
  updater.install("discord_voice", false, { version: 7 });
  updater.install("discord_missing", false);
  assert.equal(installed.length, 0);
  await new Promise((resolve) => queueMicrotask(resolve));
  assert.equal(installed[0].succeeded, true);
  assert.equal(installed[1].succeeded, false);
});

test("native module paths stay anchored and the selected runtime does not trigger false updates", async () => {
  const { updater, pending, checked } = fixture(
    { DPE_RUNTIME_SOURCE: "/signed-cache/electron" },
    "/profile/dpe-updater/installations/running",
  );
  updater.checkForUpdates();
  await pending
    .shift()
    .done({ host: "/profile/generations/old", runtime: "/signed-cache/electron" });
  assert.equal(checked[0].updateCount, 0);
  assert.equal(
    updater.getModulePath("discord_voice"),
    "/profile/dpe-updater/installations/running/modules/discord_voice-7/discord_voice",
  );
});

test("checks serialize, prepare in the background, and advertise module or runtime updates", async () => {
  const { updater, calls, pending, tick, checked, downloaded } = fixture();
  tick();
  updater.checkForUpdates();
  assert.equal(pending.length, 1);
  const first = pending.shift();
  assert.deepEqual(
    { ...first.options },
    {
      root: "/portable app",
      clientRoot: "/profile",
      channel: "canary",
      prepareOnly: true,
    },
  );
  await first.done({ host: "/profile/generations/new", runtime: "/portable app/runtime/electron" });
  assert.equal(checked[0].succeeded, true);
  assert.equal(checked[0].updateCount, 1);
  assert.equal(downloaded.length, 1);
  assert.equal(
    updater.getModulePath("discord_voice"),
    "/profile/generations/old/modules/discord_voice",
  );
  updater.checkForUpdates();
  await pending.shift().done({ host: "/profile/generations/old", runtime: "/new/electron" });
  assert.equal(checked[1].updateCount, 1);
  updater.quitAndInstallUpdates();
  assert.deepEqual(calls, ["relaunch", "quit"]);
  updater.checkForUpdates();
  await pending.shift().done(null, new Error("offline"));
  assert.equal(checked[2].succeeded, false);
  assert.equal(checked[2].updateCount, 0);
  assert.equal(downloaded.length, 2);
});

test("unchanged or explicitly offline clients do not advertise an update", async () => {
  const offline = fixture({ DPE_CLIENT_OFFLINE: "1" });
  offline.tick();
  assert.equal(offline.pending.length, 0);
  const { updater, pending, checked } = fixture();
  updater.checkForUpdates();
  await pending
    .shift()
    .done({ host: "/profile/generations/old", runtime: "/portable app/runtime/electron" });
  assert.equal(checked[0].updateCount, 0);
});

test("quitting cancels active preparation, waits for cleanup, and stops future checks", async () => {
  const { app, updater, calls, checked, tick, pending } = fixture();
  updater.checkForUpdates();
  app.emit("before-quit", { preventDefault: () => calls.push("wait") });
  assert.deepEqual(calls, ["wait", "cancel"]);
  await new Promise(setImmediate);
  assert.deepEqual(calls, ["wait", "cancel", "quit"]);
  assert.equal(checked.length, 0);
  tick();
  assert.equal(pending.length, 1);
});
