"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const turn = () => new Promise((resolve) => setImmediate(resolve));
function rig({ method = "wayland-direct", catalog = [method], experimental = true } = {}) {
  const ipc = new EventEmitter(),
    calls = [],
    requests = new Map();
  let callback;
  const adapter = {
    finish: (...args) => {
      calls.push(["finish", ...args]);
      return true;
    },
    cancel: (id) => {
      calls.push(["cancel", id]);
      return true;
    },
  };
  ipc.send = (...args) => calls.push(["send", ...args]);
  ipc.invoke = (name, value) => {
    if (name === "dpe-capture-methods") return Promise.resolve(catalog);
    assert.equal(name, "dpe-capture-inline-request");
    return new Promise((resolve) => requests.set(value.requestId, resolve));
  };
  const voice = { releaseNativeDesktopVideoSourcePickerStream: () => calls.push(["release"]) };
  const context = {
    module: { exports: {} },
    process: { platform: "linux", env: { DPE_APP_ROOT: "/app" } },
    console,
    require(name) {
      if (name === "node:path") return path;
      if (name === "electron") return { ipcRenderer: ipc };
      if (name === "./dpe-screenshare-settings.cjs")
        return { read: () => ({ captureMethod: method, experimentalCapture: experimental }) };
      if (name === "./dpe-capture-picker.cjs")
        return {
          load(_directory, _helper, onEvent) {
            callback = onEvent;
            return adapter;
          },
        };
      throw new Error(name);
    },
  };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "../../integration/video/capture-backends.cjs"), "utf8"),
    context,
  );
  context.module.exports.install("/voice", voice);
  return {
    calls,
    ipc,
    voice,
    api: context.module.exports,
    request: (id) => callback({ event: "request", requestId: id, sourceType: 0 }),
    event: (event, id) => callback({ event, requestId: id, sourceType: 0 }),
    choose(id, fields = {}) {
      requests.get(id)({
        action: "selected",
        method,
        sourceType: "screen",
        socketPath: "/run/pw",
        nodeId: id + 40,
        ...fields,
      });
    },
  };
}

test("KMS availability and actual selected handoff are reported without guessing an active method", async () => {
  const r = rig({ method: "kms", catalog: { methods: ["kms"], methodReasons: {} } });
  await turn();
  assert.deepEqual(Array.from(r.api.methods()), ["kms"]);
  assert.equal(r.api.snapshot(), null);
  const pending = r.request(1);
  r.choose(1, { handoff: "copy" });
  await pending;
  assert.equal(r.api.snapshot(), null, "selected does not mean native consumption started");
  await r.event("started", 1);
  assert.equal(r.api.snapshot().method, "kms");
  assert.equal(r.api.snapshot().sourceType, "screen");
  assert.equal(r.api.snapshot().handoff, "copy");
  await r.event("stopped", 1);
  assert.equal(r.api.snapshot(), null);
});

test("KMS reasons retain fixed codes while unarmed requests retain Vanilla capture", async () => {
  for (const reason of [
    "permission-required",
    "permission-blocked",
    "authorization-unavailable",
    "native-picker-unavailable",
    "/private/path",
  ]) {
    const r = rig({ method: "kms", catalog: { methods: [], methodReasons: { kms: reason } } });
    await turn();
    assert.equal(r.api.methods().length, 0);
    assert.equal(r.api.reasons().kms, reason === "/private/path" ? undefined : reason);
    const pending = r.request(1);
    r.choose(1, { action: "native" });
    await pending;
    assert.deepEqual(r.calls.find((item) => item[0] === "finish").slice(0, 3), [
      "finish",
      1,
      "native",
    ]);
    assert.equal(r.api.snapshot(), null);
  }
});

test("main timeout retires an unconsumed selection once without global portal release", async () => {
  const r = rig();
  await turn();
  const pending = r.request(1);
  r.choose(1);
  await pending;
  r.ipc.emit("dpe-capture-ended", {}, { requestId: 1, reason: "ended" });
  r.ipc.emit("dpe-capture-ended", {}, { requestId: 1, reason: "ended" });
  assert.deepEqual(
    r.calls.filter((item) => item[0] === "cancel"),
    [["cancel", 1]],
  );
  assert.equal(
    r.calls.some((item) => item[0] === "release"),
    false,
  );
  assert.equal(r.api.snapshot(), null);
});

test("ended pending requests discard late selections instead of reopening their FD", async () => {
  const r = rig();
  await turn();
  const pending = r.request(1);
  r.ipc.emit("dpe-capture-ended", {}, { requestId: 1 });
  r.choose(1);
  await pending;
  assert.deepEqual(
    r.calls.filter((item) => item[0] === "cancel"),
    [["cancel", 1]],
  );
  assert.equal(
    r.calls.some((item) => item[0] === "finish"),
    false,
  );
});

test("supersession ignores stale endings and active source loss never cancels its native FD", async () => {
  const r = rig();
  await turn();
  let pending = r.request(1);
  r.choose(1);
  await pending;
  pending = r.request(2);
  r.choose(2);
  await pending;
  r.ipc.emit("dpe-capture-ended", {}, { requestId: 1 });
  assert.deepEqual(
    r.calls.filter((item) => item[0] === "cancel"),
    [["cancel", 1]],
  );
  const lifecycle = [];
  const unsubscribe = r.api.subscribe((event) => lifecycle.push(event));
  await r.event("started", 2);
  assert.equal(r.api.snapshot().requestId, 2);
  r.ipc.emit("dpe-capture-ended", {}, { requestId: 2 });
  r.ipc.emit("dpe-capture-ended", {}, { requestId: 2 });
  assert.equal(r.api.snapshot(), null);
  assert.deepEqual(JSON.parse(JSON.stringify(lifecycle)), [
    { event: "started", requestId: 2 },
    { event: "ended", requestId: 2 },
  ]);
  unsubscribe();
  assert.deepEqual(
    r.calls.filter((item) => item[0] === "cancel"),
    [["cancel", 1]],
  );
  assert.equal(
    r.calls.some((item) => item[0] === "release"),
    false,
  );
  r.voice.releaseNativeDesktopVideoSourcePickerStream();
  assert.equal(r.calls.filter((item) => item[0] === "release").length, 1);
  assert.deepEqual(
    r.calls.filter((item) => item[0] === "cancel"),
    [["cancel", 1]],
  );
  await r.event("stopped", 2);
  assert.equal(r.api.snapshot(), null);
});

test("experimental off ignores legacy custom defaults and preserves native capture", async () => {
  const r = rig({ method: "kms", experimental: false });
  await r.request(1);
  assert.deepEqual(
    r.calls.find((item) => item[0] === "finish"),
    ["finish", 1, "native"],
  );
});

test("game process identity reaches audio only while native capture is active", async () => {
  const r = rig({ method: "vkcapture" });
  await turn();
  const pending = r.request(1);
  const identity = { pid: 200, start: "2000" };
  r.choose(1, { sourceType: "window", processIdentity: identity });
  await pending;
  assert.equal(r.api.snapshot(), null);
  await r.event("started", 1);
  assert.equal(r.api.snapshot().processIdentity, identity);
  await r.event("stopped", 1);
  assert.equal(r.api.snapshot(), null);
});
