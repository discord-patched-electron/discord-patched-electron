const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const source = require("../../integration/discord-ui/source.cjs");
const streamAudio = require("../../integration/discord-ui/stream-audio.cjs");

function fixture({ loaded = true, ambiguous = false } = {}) {
  const calls = [],
    tracked = [],
    initialized = [];
  const context = vm.createContext({ calls, tracked });
  // Preserve the observed native action's semantic signature, not its minified name.
  const action = () =>
    vm.runInContext(
      `(function(e) {
    if (e.noTrack !== true) tracked.push(e.soundshareEnabled);
    const dispatcher = { dispatch: value => calls.push(value) };
    dispatcher.dispatch({ type: "STREAM_UPDATE_SETTINGS", ...e });
  })`,
      context,
    );
  const runtime = (id) => {
    initialized.push(id);
    throw Error("must not initialize modules");
  };
  runtime.c = loaded ? { loaded: { exports: { Xd: action() } } } : {};
  if (ambiguous) runtime.c.other = { exports: { Xd: action() } };
  runtime.m = {
    unrelated: () => {
      throw Error("must not start video or enumerate devices");
    },
  };
  const chunks = [];
  chunks.push = function (chunk) {
    Array.prototype.push.call(this, chunk);
    return chunk[2]?.(runtime);
  };
  context.webpackChunkdiscord_app = chunks;
  return {
    calls,
    tracked,
    initialized,
    run: (action) => vm.runInContext(source.call(streamAudio, { action }), context),
  };
}

test("native stream-audio action disables the checkbox without changing quality or starting capture", () => {
  const f = fixture();
  assert.equal(f.run("probe"), true);
  assert.equal(f.calls.length, 0, "read-only discovery never changes preferences");
  assert.equal(f.run("disable"), true);
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls)), [
    { type: "STREAM_UPDATE_SETTINGS", soundshareEnabled: false, noTrack: true },
  ]);
  assert.deepEqual(f.tracked, []);
  assert.deepEqual(f.initialized, []);
});

test("missing or ambiguous native stream actions fail closed without executing factories", () => {
  for (const options of [{ loaded: false }, { ambiguous: true }]) {
    const f = fixture(options);
    assert.equal(f.run("disable"), false);
    assert.deepEqual(f.calls, []);
    assert.deepEqual(f.initialized, []);
  }
});
