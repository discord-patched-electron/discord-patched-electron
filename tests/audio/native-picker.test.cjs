const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const { show } = require("../../integration/audio/native-picker-bridge.cjs");
const render = require("../../integration/audio/native-picker-renderer.cjs");
const discover = require("../../integration/audio/native-picker-discovery.cjs");
const tick = () => new Promise(setImmediate);
const options = [{ id: "system" }, { id: "42", label: "Player", iconNames: ["player"] }];
function fixture(execute, resolveIcon = async () => null) {
  const replies = [],
    requests = [];
  let fallbacks = 0,
    closedFallbacks = 0;
  const close = show(options, (value) => replies.push(value), {
    execute(code) {
      const request = JSON.parse(
        code.match(/\}\)\((\{"operation".*?\}),function discoverPicker/s)[1],
      );
      requests.push(request);
      return execute(request);
    },
    resolveIcon,
    fallback() {
      fallbacks++;
      return () => closedFallbacks++;
    },
  });
  return {
    close,
    replies,
    requests,
    get fallbacks() {
      return fallbacks;
    },
    get closedFallbacks() {
      return closedFallbacks;
    },
  };
}
test("native replies accept only offered sources and known actions", async () => {
  for (const selection of ["42", "system", "none", "refresh"]) {
    const f = fixture(() => ({ selection }));
    await tick();
    assert.deepEqual(f.replies, [selection]);
    assert.equal(f.fallbacks, 0);
    f.close();
  }
  const f = fixture(() => ({ selection: "unoffered" }));
  await tick();
  assert.deepEqual(f.replies, []);
  assert.equal(f.fallbacks, 1);
  f.close();
});
test("missing or changed components preserve the original DOM design and teardown once", async () => {
  for (const execute of [
    () => ({ unavailable: true }),
    () => {
      throw Error("context gone");
    },
  ]) {
    const f = fixture(execute);
    await tick();
    assert.equal(f.fallbacks, 1);
    f.close();
    f.close();
    await tick();
    assert.equal(f.closedFallbacks, 1);
    assert.equal(f.requests.filter((r) => r.operation === "close").length, 1);
    assert.deepEqual(f.replies, []);
  }
});
test("helper teardown ignores late native selections, fallbacks and artwork", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  for (const result of [{ selection: "42" }, { unavailable: true }]) {
    let finish, icon;
    const f = fixture(
      (r) =>
        r.operation === "open"
          ? new Promise((resolve) => {
              finish = resolve;
            })
          : r.operation === "status"
            ? { mounted: true }
            : undefined,
      () =>
        new Promise((resolve) => {
          icon = resolve;
        }),
    );
    await tick();
    t.mock.timers.tick(200);
    await tick();
    f.close();
    finish(result);
    icon("data:image/png;base64,aQ==");
    await tick();
    assert.deepEqual(f.replies, []);
    assert.equal(f.fallbacks, 0);
    assert.equal(
      f.requests.some((r) => r.operation === "icon"),
      false,
    );
  }
});
test("fallback failure requests the external picker", async () => {
  const replies = [];
  const close = show(options, (v) => replies.push(v), {
    execute: () => ({ unavailable: true }),
    resolveIcon: async () => null,
    fallback() {
      throw Error("no dialog");
    },
  });
  await tick();
  assert.deepEqual(replies, ["fallback"]);
  close();
});
test("no Discord module cache returns unavailable without executing app modules", () => {
  for (const environment of [{}, { webpackChunkdiscord_app: [] }])
    assert.equal(
      vm.runInNewContext(
        `(${render.toString()})({operation:'open',key:'test',options:[]},${discover.toString()})`,
        environment,
      ).unavailable,
      true,
    );
});
test("a stalled open/status execution falls back once and ignores late native replies", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let finish;
  const f = fixture((r) =>
    r.operation === "open"
      ? new Promise((resolve) => {
          finish = resolve;
        })
      : new Promise(() => {}),
  );
  await tick();
  t.mock.timers.tick(4000);
  await tick();
  assert.equal(f.fallbacks, 1);
  finish({ selection: "42" });
  await tick();
  assert.deepEqual(f.replies, []);
  f.close();
  assert.equal(f.closedFallbacks, 1);
});
test("validated mounted dialogs never time out while the user is choosing", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const f = fixture((r) => (r.operation === "open" ? new Promise(() => {}) : { mounted: true }));
  await tick();
  t.mock.timers.tick(200);
  await tick();
  t.mock.timers.tick(60000);
  await tick();
  assert.equal(f.fallbacks, 0);
  f.close();
});
test("missing startup modules get bounded retries but ambiguous matches do not", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let attempts = 0;
  const f = fixture((r) =>
    r.operation === "open"
      ? ++attempts < 3
        ? { unavailable: true, reason: "missing-components" }
        : { selection: "42" }
      : { mounted: false },
  );
  await tick();
  t.mock.timers.tick(200);
  await tick();
  t.mock.timers.tick(200);
  await tick();
  assert.equal(attempts, 3);
  assert.deepEqual(f.replies, ["42"]);
  assert.equal(f.fallbacks, 0);
  f.close();
  const ambiguous = fixture(() => ({ unavailable: true, reason: "ambiguous-components" }));
  await tick();
  assert.equal(ambiguous.fallbacks, 1);
  assert.equal(ambiguous.requests.filter((r) => r.operation === "open").length, 1);
  ambiguous.close();
});
test("closing before a delayed page open tombstones that session", () => {
  const context = vm.createContext({});
  const invoke = (request) =>
    vm.runInContext(
      `(${render.toString()})(${JSON.stringify(request)},()=>{throw Error('must not discover a cancelled open')})`,
      context,
    );
  invoke({ operation: "close", key: "late" });
  assert.equal(invoke({ operation: "open", key: "late" }).cancelled, true);
  assert.equal(invoke({ operation: "status", key: "late" }).mounted, false);
});
