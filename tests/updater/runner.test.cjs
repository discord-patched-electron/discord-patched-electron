const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { PassThrough } = require("node:stream");
const { test } = require("node:test");
const runUpdate = require("../../integration/updater/runner.cjs");

function fixture() {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.kill = () => child.emit("close", null);
  const states = [];
  const task = runUpdate(
    {
      root: "/app",
      clientRoot: "/profile/dpe-updater",
      channel: "canary",
      onProgress: (state) => states.push(state),
    },
    (command, args, options) => {
      assert.equal(command, "python3");
      assert.deepEqual(args, [
        "/app/integration/updater/check.py",
        "/app",
        "/profile/dpe-updater",
        "canary",
      ]);
      assert.equal(options.env.PYTHONPATH, "/app/python");
      assert.equal(options.env.PYTHONNOUSERSITE, "1");
      assert.equal(options.env.DPE_UPDATE_PROGRESS, "1");
      assert.equal(options.env.LD_PRELOAD, "");
      assert.equal(options.env.PYTHONHOME, "");
      assert.equal(options.env.DPE_PREPARE_ONLY, "0");
      return child;
    },
  );
  return { child, states, ...task };
}

test("startup streams fragmented progress and waits for a complete result", async () => {
  const { child, states, promise } = fixture();
  child.stdout.write('{"type":"progress","stage":"download",');
  assert.equal(states.length, 0);
  child.stdout.write('"label":"Patched Electron","current":40,"total":100}\n');
  assert.equal(states[0].label, "Patched Electron");
  const prepared = {
    host: "/client/new",
    runtime: "/runtime/new",
    installed_runtime: "/profile/app/electron",
    voice: "/profile/app/voice",
  };
  child.stdout.end(JSON.stringify(prepared) + "\n");
  child.emit("close", 0);
  assert.deepEqual(await promise, prepared);
});

test("missing Python and incomplete results become retryable errors", async () => {
  const missing = fixture();
  missing.child.emit("error", Object.assign(new Error("spawn failed"), { code: "ENOENT" }));
  await assert.rejects(missing.promise, /Python 3 is missing/);
  const incomplete = fixture();
  incomplete.child.emit("close", 0);
  await assert.rejects(incomplete.promise, /did not finish/);
});

test("invalid output and cancellation stop preparation instead of launching Discord", async () => {
  const invalid = fixture();
  invalid.child.stdout.write('{"host":"relative","runtime":"relative"}\n');
  await assert.rejects(invalid.promise, /invalid result/);
  const cancelled = fixture();
  cancelled.cancel();
  await assert.rejects(cancelled.promise, /cancelled/);
});

test("cancellation terminates a helper group even when its children ignore SIGTERM", async () => {
  const { spawn } = require("node:child_process");
  const fs = require("node:fs");
  let task;
  const pids = [];
  task = runUpdate(
    {
      root: "/fixture",
      clientRoot: "/fixture/profile",
      channel: "canary",
      prepareOnly: true,
      killDelayMs: 50,
      timeoutMs: 5000,
      onProgress(state) {
        pids.push(state.pid);
        task.cancel();
      },
    },
    (_command, _args, options) => {
      assert.equal(options.env.DPE_PREPARE_ONLY, "1");
      const childCode = `process.on('SIGTERM', () => {}); setInterval(() => {}, 1000);
      console.log(JSON.stringify({type: 'progress', pid: process.pid}));`;
      const parentCode = `process.on('SIGTERM', () => {}); setInterval(() => {}, 1000);
      require('node:child_process').spawn(process.execPath, ['-e', ${JSON.stringify(childCode)}], {stdio: 'inherit'});`;
      const child = spawn(process.execPath, ["-e", parentCode], options);
      pids.push(child.pid);
      return child;
    },
  );
  await assert.rejects(task.promise, /cancelled/);
  assert.equal(pids.length, 2);
  for (const pid of pids) {
    try {
      // An adopted child can briefly remain a zombie until init reaps it.
      assert.match(fs.readFileSync(`/proc/${pid}/stat`, "utf8"), /\) Z /);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }
});
