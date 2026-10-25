const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

function fixture(env = {}, installedHost) {
  const calls = [];
  const app = { relaunch: () => calls.push("relaunch"), quit: () => calls.push("quit") };
  const module = { exports: {} };
  const pending = [];
  let tick;
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "../../integration/updater/module-updater.cjs"), "utf8"),
    {
      module,
      require: (name) =>
        name === "node:child_process"
          ? {
              execFile: (...args) => pending.push(args),
            }
          : require(name),
      process: { env, execPath: "/portable app/runtime/electron" },
      queueMicrotask,
      console: { error: () => {} },
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
  return { updater, calls, pending, tick };
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

test("native module paths remain anchored to the running install after an update", () => {
  const { updater, pending } = fixture(
    { DPE_RUNTIME_SOURCE: "/signed-cache/electron" },
    "/profile/dpe-updater/installations/running",
  );
  const checked = [];
  updater.events.on(updater.UPDATE_CHECK_FINISHED, (event) => checked.push(event));
  updater.checkForUpdates();
  pending.shift()[3](
    null,
    JSON.stringify({ host: "/profile/generations/old", runtime: "/signed-cache/electron" }),
    "",
  );
  assert.equal(checked[0].updateCount, 0);
  assert.equal(
    updater.getModulePath("discord_voice"),
    "/profile/dpe-updater/installations/running/modules/discord_voice-7/discord_voice",
  );
});

test("checks serialize, advertise prepared generations, and restart through app.relaunch", () => {
  const { updater, calls, pending, tick } = fixture({ LD_PRELOAD: "/helper.so" });
  const checked = [],
    downloaded = [];
  updater.events.on(updater.UPDATE_CHECK_FINISHED, (event) => checked.push(event));
  updater.events.on(updater.DOWNLOADING_MODULES_FINISHED, (event) => downloaded.push(event));
  tick();
  updater.checkForUpdates();
  assert.equal(pending.length, 1);
  const [command, args, options, done] = pending.shift();
  assert.equal(command, "python3");
  assert.deepEqual(Array.from(args), [
    "/portable app/integration/updater/check.py",
    "/portable app",
    "/profile",
    "canary",
  ]);
  assert.equal(options.env.LD_PRELOAD, "");
  assert.equal(options.env.DPE_PREPARE_ONLY, "1");
  done(
    null,
    JSON.stringify({ host: "/profile/generations/new", runtime: "/portable app/runtime/electron" }),
    "",
  );
  assert.equal(checked[0].succeeded, true);
  assert.equal(checked[0].updateCount, 1);
  assert.equal(downloaded.length, 1);
  // A running client must keep its original module paths until it restarts.
  assert.equal(
    updater.getModulePath("discord_voice"),
    "/profile/generations/old/modules/discord_voice",
  );
  updater.quitAndInstallUpdates();
  assert.deepEqual(calls, ["relaunch", "quit"]);
  updater.checkForUpdates();
  pending.shift()[3](new Error("offline"), "", "offline");
  assert.equal(checked[1].succeeded, false);
  assert.equal(checked[1].updateCount, 0);
  assert.equal(downloaded.length, 1);
});

test("unchanged or explicitly offline clients do not advertise an update", () => {
  const offline = fixture({ DPE_CLIENT_OFFLINE: "1" });
  offline.tick();
  assert.equal(offline.pending.length, 0);
  const { updater, pending } = fixture();
  updater.events.on(updater.UPDATE_CHECK_FINISHED, (event) => assert.equal(event.updateCount, 0));
  updater.checkForUpdates();
  pending.shift()[3](
    null,
    JSON.stringify({ host: "/profile/generations/old", runtime: "/portable app/runtime/electron" }),
    "",
  );
});

test("Electron-only updates use the same restart notification and reject invalid results", () => {
  const { updater, pending } = fixture();
  const checked = [];
  updater.events.on(updater.UPDATE_CHECK_FINISHED, (event) => checked.push(event));
  updater.checkForUpdates();
  pending.shift()[3](
    null,
    JSON.stringify({
      host: "/profile/generations/old",
      runtime: "/profile/dpe-runtime/new/electron",
    }),
    "",
  );
  assert.equal(checked[0].succeeded, true);
  assert.equal(checked[0].updateCount, 1);
  updater.checkForUpdates();
  pending.shift()[3](null, "not a prepared update", "");
  assert.equal(checked[1].succeeded, false);
  assert.equal(checked[1].updateCount, 0);
});
