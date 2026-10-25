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
  child.stdout.end('{"host":"/client/new","runtime":"/runtime/new"}\n');
  child.emit("close", 0);
  assert.deepEqual(await promise, { host: "/client/new", runtime: "/runtime/new" });
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
