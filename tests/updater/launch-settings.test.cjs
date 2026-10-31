const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

function fixture() {
  let response = 0;
  const prompts = [];
  const calls = [];
  const handlers = new Map();
  const ipc = Object.assign(new EventEmitter(), {
    handle: (name, callback) => handlers.set(name, callback),
  });
  const window = new EventEmitter();
  window.webContents = Object.assign(new EventEmitter(), {
    mainFrame: {},
    ipc,
    send() {},
  });
  const mods = {
    isOnboarding: () => false,
    isOnboardingSettings: () => false,
    finishSettings: async (current) => {
      assert.equal(current, window);
      calls.push("finish");
      return { completed: true };
    },
  };
  const context = {
    require(name) {
      if (name === "electron")
        return {
          app: {},
          dialog: {
            showMessageBoxSync: (_window, options) => {
              prompts.push(options);
              return response;
            },
          },
        };
      if (name === "node:child_process") return {};
      if (name === "node:path") return path;
      if (name === "./profile.cjs") return {};
      if (name === "./mod-settings.cjs") return mods;
      if (name === "./relaunch.cjs") return { restart: () => calls.push("restart") };
      throw Error("Unexpected dependency " + name);
    },
    module: { exports: {} },
    global: {},
    process: { env: {} },
  };
  vm.runInNewContext(fs.readFileSync("integration/updater/launch-settings.cjs", "utf8"), context);
  const api = context.module.exports;
  api.attach(window);
  const event = { sender: window.webContents, senderFrame: window.webContents.mainFrame };
  return {
    api,
    window,
    ipc,
    event,
    prompts,
    handlers,
    calls,
    mods,
    set response(value) {
      response = value;
    },
  };
}

test("onboarding close confirms unsaved flags once, rejects foreign dirty state and permits retry", () => {
  const f = fixture();
  f.ipc.emit("dpe-launch-dirty", { sender: {}, senderFrame: {} }, true);
  assert.equal(f.api.confirmClose(f.window), true);
  assert.equal(f.prompts.length, 0);
  f.ipc.emit("dpe-launch-dirty", f.event, true);
  assert.equal(f.api.confirmClose(f.window), false);
  f.response = 1;
  assert.equal(f.api.confirmClose(f.window), true);
  let allowed = false;
  f.window.webContents.emit("will-prevent-unload", {
    preventDefault: () => {
      allowed = true;
    },
  });
  assert.equal(allowed, true);
  assert.equal(f.prompts.length, 2);
  f.api.resetCloseApproval(f.window);
  f.response = 0;
  assert.equal(f.api.confirmClose(f.window), false);
  assert.equal(f.prompts.length, 3);
  f.ipc.emit("dpe-launch-dirty", f.event, false);
  assert.equal(f.api.confirmClose(f.window), true);
});

test("Restart Discord delegates only onboarding Settings to guarded completion", async () => {
  const f = fixture();
  const restart = f.handlers.get("dpe-launch-restart");
  assert.equal((await restart(f.event)).restarting, true);
  assert.deepEqual(f.calls, ["restart"]);
  f.mods.isOnboarding = () => true;
  assert.match((await restart(f.event)).error, /Close Settings/);
  f.mods.isOnboardingSettings = () => true;
  assert.equal((await restart(f.event)).completed, true);
  assert.deepEqual(f.calls, ["restart", "finish"]);
});
