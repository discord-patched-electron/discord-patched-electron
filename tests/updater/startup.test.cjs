const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const failure = require("../../integration/updater/failure.cjs");

async function fixture(config = {}) {
  const exits = [],
    states = [],
    pending = [],
    timers = new Set();
  const app = Object.assign(new EventEmitter(), {
    setPath() {},
    requestSingleInstanceLock: () => true,
    whenReady: () => Promise.resolve(),
    exit: (code) => exits.push(code),
  });
  const window = Object.assign(new EventEmitter(), {
    webContents: new EventEmitter(),
    isDestroyed: () => false,
    close() {
      this.emit("closed");
    },
  });
  let action;
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "../../integration/updater/startup.cjs"), "utf8"),
    {
      require(name) {
        if (name === "electron") return { app };
        if (name === "node:fs")
          return {
            mkdirSync() {},
            existsSync: () => true,
            readFileSync: () => '{"releaseChannel":"canary"}',
          };
        if (name === "./window.cjs")
          return (handle) => {
            action = handle;
            return { window, update: (state) => states.push(state) };
          };
        if (name === "./profile.cjs") return { settings: () => ({}), config: () => config };
        if (name === "./failure.cjs") return failure;
        if (name === "./runner.cjs")
          return (options) => {
            let resolve, reject;
            const promise = new Promise((yes, no) => {
              resolve = yes;
              reject = no;
            });
            const task = {
              promise,
              options,
              resolve,
              reject,
              cancelled: false,
              cancel() {
                this.cancelled = true;
              },
            };
            pending.push(task);
            return task;
          };
        return require(name);
      },
      process: {
        env: {
          DPE_APP_ROOT: "/app",
          DPE_PROFILE_DIR: "/profile",
          DPE_CLIENT_ROOT: "/profile/dpe-updater",
        },
        argv: [],
        once() {},
      },
      console: { error() {} },
      setTimeout(callback, ms) {
        const timer = { callback, ms };
        timers.add(timer);
        return timer;
      },
      clearTimeout(timer) {
        timers.delete(timer);
      },
    },
  );
  await new Promise(setImmediate);
  window.webContents.emit("dom-ready");
  return { action, states, pending, timers, exits, window };
}

test("startup exposes errors, can pause automatic retries, and retries on demand", async () => {
  const f = await fixture();
  f.pending[0].reject(new Error("connection reset"));
  await new Promise(setImmediate);
  assert.equal(f.states.at(-1).stage, "error");
  assert.match(f.states.at(-1).message, /Check your connection/);
  assert.equal([...f.timers][0].ms, 10000);
  f.action("pause");
  assert.equal(f.timers.size, 0);
  assert.equal(f.states.at(-1).seconds, null);
  f.action("retry");
  f.action("retry");
  assert.equal(f.pending.length, 2);
  f.pending[1].resolve({});
  await new Promise(setImmediate);
  assert.deepEqual(f.exits, [0]);
});

test("permanent startup failures wait for user action and quit waits for cancellation", async () => {
  const f = await fixture();
  f.pending[0].reject(
    new Error("Brotli is missing. Install it with your package manager, then try again."),
  );
  await new Promise(setImmediate);
  assert.equal(f.timers.size, 0);
  assert.match(f.states.at(-1).message, /Install it/);
  f.action("retry");
  f.window.close();
  assert.equal(f.pending[1].cancelled, true);
  assert.equal(f.exits.length, 0);
  f.pending[1].reject(new Error("Update cancelled."));
  await new Promise(setImmediate);
  assert.deepEqual(f.exits, [1]);
  assert.equal(f.timers.size, 0);
});

test("quickstart falls back to online preparation once when its cache is incomplete", async () => {
  const f = await fixture({ quickstart: true });
  assert.equal(f.pending[0].options.offline, true);
  f.pending[0].reject(new Error("cache incomplete"));
  await new Promise(setImmediate);
  const timer = [...f.timers][0];
  assert.equal(timer.ms, 0);
  timer.callback();
  assert.equal(f.pending[1].options.offline, false);
  f.pending[1].resolve({});
  await new Promise(setImmediate);
  assert.deepEqual(f.exits, [0]);
});

test("failure guidance distinguishes space, permissions, offline setup and verification", () => {
  for (const [details, message] of [
    ["[Errno 28] No space left on device", /Free up space/],
    ["Permission denied: profile", /writable/],
    ["First launch requires an internet connection", /DPE_CLIENT_OFFLINE/],
    ["Unsupported Discord client layout", /latest package/],
    ["Unsupported PulseAudio mixer layout: unknown Attach instructions", /latest package/],
  ]) {
    const state = failure(new Error(details));
    assert.match(state.message, message);
    assert.equal(state.seconds, null);
    assert.equal(state.details, details);
  }
});

test("quitting as preparation completes never hands a successful launch to the shell", async () => {
  const f = await fixture();
  f.pending[0].resolve({});
  f.window.close();
  await new Promise(setImmediate);
  assert.deepEqual(f.exits, [1]);
  assert.equal(f.timers.size, 0);
});
