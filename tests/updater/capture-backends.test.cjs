"use strict";
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const {
  createController,
  worker,
  rendererCapabilities,
} = require("../../integration/updater/capture-backends.cjs");

const turn = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function owner(id = 1) {
  const result = new EventEmitter();
  result.id = id;
  result.dead = false;
  result.messages = [];
  result.send = (...message) => result.messages.push(message);
  result.isDestroyed = () => result.dead;
  result.destroy = () => {
    result.dead = true;
    result.emit("destroyed");
  };
  return result;
}
function helper() {
  const events = new EventEmitter();
  return {
    events,
    commands: [],
    sent: [],
    timeouts: [],
    stops: 0,
    wait(predicate, timeout, signal) {
      this.timeouts.push(timeout);
      return new Promise((resolve, reject) => {
        const cleanup = () => {
          events.off("message", receive);
          events.off("failure", fail);
          signal?.removeEventListener("abort", abort);
        };
        const receive = (message) => {
          if (predicate(message)) {
            cleanup();
            resolve(message);
          }
        };
        const fail = (error) => {
          cleanup();
          reject(error);
        };
        const abort = () => fail(new Error("cancelled"));
        events.on("message", receive);
        events.on("failure", fail);
        if (signal?.aborted) abort();
        else signal?.addEventListener("abort", abort, { once: true });
      });
    },
    async command(value) {
      this.commands.push(value);
      if (value.op === "list")
        return { sources: [{ token: "game", name: "Synthetic game", available: true }] };
      if (value.op === "start" && this.startError) throw this.startError;
      return { ok: true };
    },
    send(value) {
      this.sent.push(value);
    },
    stop() {
      this.stops++;
      events.emit("failure", new Error("stopped"));
    },
  };
}
function rig(options = {}) {
  const window = new EventEmitter(),
    runs = [];
  let opens = 0;
  const controller = createController({
    root: "/test/app",
    env: { WAYLAND_DISPLAY: "wayland-test", XDG_RUNTIME_DIR: "/run/user/test" },
    exists: () => true,
    permission: async () => ({ allowed: true }),
    list: async () => ({
      available: true,
      outputs: [{ id: "output-1", name: "Test\nMonitor", supported: true }],
    }),
    openSettings() {
      opens++;
      return window;
    },
    run(file, args) {
      const result = helper();
      runs.push({ file, args, helper: result });
      return result;
    },
    ...options,
  });
  return { controller, window, runs, opens: () => opens };
}
async function request(r, who, method = "wayland-direct", id = 1) {
  const response = r.controller.request(who, { requestId: id, sourceType: 0, method, fps: 30 });
  await turn();
  return { response, state: r.controller.state() };
}

const kmsListing = {
  version: 1,
  backend: "kms",
  available: true,
  outputs: [{ id: "kms-monitor-token", name: "KMS monitor", supported: true }],
};

test("picker thumbnails are bounded, owner-scoped, cancellable and never prompt", async () => {
  const pending = deferred();
  let calls = 0,
    signal;
  const r = rig({
    experimental: () => true,
    permission: async () => ({ allowed: true }),
    thumbnail: async (_file, args, _env, abort) => {
      calls++;
      signal = abort;
      assert.deepEqual(args.slice(-4), ["--width", "320", "--height", "180"]);
      return pending.promise;
    },
    encodeThumbnail: () => {
      throw Error("cancelled pixels must not be delivered");
    },
  });
  const who = owner(),
    other = owner(2);
  try {
    const catalog = await r.controller.catalog(who);
    const token = catalog.sources.find((source) => source.kind === "screen").token;
    assert.deepEqual(await r.controller.preview(other, { token, width: 320, height: 180 }), {});
    assert.deepEqual(await r.controller.preview(who, { token, width: 10000, height: 180 }), {});
    assert.equal(calls, 0);
    const work = r.controller.preview(who, { token, width: 320, height: 180 });
    await turn();
    assert.equal(calls, 1);
    assert.deepEqual(await r.controller.preview(who, { token, width: 320, height: 180 }), {});
    await r.controller.stopPreview(other);
    assert.equal(signal.aborted, false);
    who.destroy();
    assert.equal(signal.aborted, true);
    pending.resolve({ pixels: Buffer.alloc(4), width: 1, height: 1, format: "BGRA" });
    assert.deepEqual(await work, {});
  } finally {
    r.controller.close();
  }
});

test("revoked KMS permission prevents thumbnail acquisition", async () => {
  let allowed = true,
    calls = 0;
  const r = rig({
    experimental: () => true,
    permission: async () => ({ allowed }),
    list: async () => kmsListing,
    thumbnail: async () => {
      calls++;
      return {};
    },
  });
  try {
    const who = owner();
    const catalog = await r.controller.catalog(who);
    allowed = false;
    assert.deepEqual(
      await r.controller.preview(who, { token: catalog.sources[0].token, width: 320, height: 180 }),
      {},
    );
    assert.equal(calls, 0);
  } finally {
    r.controller.close();
  }
});

test("X11 KMS catalog waits for actual recognized-store picker support without overriding it", async () => {
  const status = { methods: ["kms"], methodReasons: {} };
  const controller = { capabilityStatus: async () => status };
  let support,
    probes = 0;
  const renderer = {
    executeJavaScript: async (source) => {
      assert.equal(source, 'Boolean(globalThis[Symbol.for("dpe.capturePolicy")]?.supported?.())');
      probes++;
      return support;
    },
  };
  for (support of [undefined, false, "true"]) {
    assert.deepEqual(await rendererCapabilities(controller, renderer, { DISPLAY: ":99" }), {
      methods: [],
      methodReasons: { kms: "native-picker-unavailable" },
    });
  }
  support = true;
  assert.deepEqual(await rendererCapabilities(controller, renderer, { DISPLAY: ":99" }), status);
  assert.deepEqual(
    await rendererCapabilities(controller, renderer, { WAYLAND_DISPLAY: "wayland-test" }),
    status,
  );
  assert.equal(probes, 4);
  renderer.executeJavaScript = async () => {
    throw new Error("store unavailable");
  };
  assert.equal(
    (await rendererCapabilities(controller, renderer, { DISPLAY: ":99" })).methodReasons.kms,
    "native-picker-unavailable",
  );
});

test("KMS inventories on Linux X11 and Wayland without starting helpers or a permission dialog", async () => {
  for (const display of [{ DISPLAY: ":99" }, { WAYLAND_DISPLAY: "wayland-test" }]) {
    const files = [];
    const r = rig({
      platform: "linux",
      env: { ...display, XDG_RUNTIME_DIR: "/run/user/test" },
      exists: (file) => file.endsWith("dpe-kms-source"),
      list: async (file) => {
        files.push(file);
        return kmsListing;
      },
    });
    try {
      assert.deepEqual(await r.controller.capabilityStatus(), {
        methods: ["kms"],
        methodReasons: {},
      });
      assert.deepEqual(files, ["/test/app/capture/dpe-kms-source"]);
      assert.equal(r.runs.length, 0);
      assert.equal(r.opens(), 0);
      assert.deepEqual(
        await r.controller.request(owner(), {
          requestId: 1,
          method: "kms",
          sourceType: 1,
        }),
        { action: "error" },
      );
      assert.equal(r.opens(), 0, "KMS cannot select an application window");
    } finally {
      r.controller.close();
    }
  }
});

test("KMS exposes only fixed unavailable reasons and never starts an unavailable backend", async () => {
  for (const reason of [
    "helper-missing",
    "permission-required",
    "permission-blocked",
    "authorization-unavailable",
    "no-outputs",
    "unsupported",
    "private-device-path",
  ]) {
    const r = rig({
      platform: "linux",
      env: { DISPLAY: ":99" },
      exists: (file) => file.endsWith("dpe-kms-source"),
      list: async () => ({ ...kmsListing, available: false, reason }),
    });
    try {
      assert.deepEqual(await r.controller.capabilityStatus(), {
        methods: [],
        methodReasons: { kms: reason === "private-device-path" ? "unsupported" : reason },
      });
      assert.deepEqual(
        await r.controller.request(owner(), {
          requestId: 1,
          method: "kms",
          sourceType: 0,
        }),
        { action: "error" },
      );
      assert.equal(r.opens(), 0);
      assert.equal(r.runs.length, 0);
    } finally {
      r.controller.close();
    }
  }
});

test("KMS selects only inventoried monitor tokens and never authorizes when permission is already available", async () => {
  const r = rig({
      platform: "linux",
      env: { DISPLAY: ":99", XDG_RUNTIME_DIR: "/run/user/test" },
      list: async () => kmsListing,
    }),
    who = owner();
  try {
    const { response, state } = await request(r, who, "kms");
    await assert.rejects(r.controller.choose(state.id, "/dev/dri/card0"), /available source/);
    assert.equal(r.runs.length, 0);
    const choosing = r.controller.choose(state.id, "kms-monitor-token", true);
    const source = r.runs[0];
    assert.equal(source.file, "/test/app/capture/dpe-kms-source");
    assert.deepEqual(source.args, [
      "--output",
      "kms-monitor-token",
      "--fps",
      "30",
      "--render-node",
      "auto",
    ]);
    assert.deepEqual(source.helper.timeouts, [10000]);
    source.helper.events.emit("message", { event: "ready", nodeId: 42, handoff: "copy" });
    await choosing;
    assert.deepEqual(await response, {
      action: "selected",
      socketPath: "/run/user/test/pipewire-0",
      nodeId: 42,
      method: "kms",
      sourceType: "screen",
      handoff: "copy",
      handoffFallback: null,
    });
    r.controller.event(who, { requestId: 1, event: "started" });
    r.controller.rate(who, 60);
    assert.deepEqual(source.helper.sent, [{ op: "rate", fps: 60 }]);
    r.controller.event(who, { requestId: 1, event: "stopped" });
    assert.equal(source.helper.stops, 1);
    assert.equal(who.listenerCount("destroyed"), 0);
    assert.equal(source.helper.events.listenerCount("message"), 0);
  } finally {
    r.controller.close();
  }
});

test("KMS startup terminal events report fixed errors and permit retry without a ready timeout", async () => {
  const r = rig({ platform: "linux", list: async () => kmsListing }),
    who = owner();
  try {
    const { response, state } = await request(r, who, "kms");
    const choosing = r.controller.choose(state.id, "kms-monitor-token");
    r.runs[0].helper.events.emit("message", {
      event: "error",
      errorCode: "permission-required",
      error: "/private/device/path",
    });
    await choosing;
    assert.equal(r.controller.state().phase, "choosing");
    assert.match(r.controller.state().error, /Allow direct screen capture/);
    assert.doesNotMatch(r.controller.state().error, /private/);
    assert.equal(r.runs[0].helper.stops, 1);
    assert.equal(r.controller.state().authorizationRequired, true);
    await r.controller.refresh();
    assert.equal(
      r.controller.state().authorizationRequired,
      true,
      "metadata cannot undo a denied capture",
    );
    await assert.rejects(
      r.controller.choose(state.id, "kms-monitor-token"),
      /Allow direct screen capture/,
    );
    assert.equal(r.runs.length, 1, "retry must wait for an explicit authorization action");
    const retry = r.controller.choose(state.id, "kms-monitor-token", true);
    assert.deepEqual(r.runs[1].args.slice(-2), ["--authorize", "true"]);
    r.runs[1].helper.events.emit("message", { event: "error", reason: "permission-blocked" });
    await retry;
    assert.equal(r.controller.state().authorizationRequired, false);
    assert.match(r.controller.state().error, /launch environment blocks KMS permissions/);
    r.controller.cancel(state.id);
    assert.deepEqual(await response, { action: "cancel" });
  } finally {
    r.controller.close();
  }
});

test("KMS authorization requires explicit boolean consent, supports cancellation and ignores late readiness", async () => {
  const r = rig({
      platform: "linux",
      list: async () => ({ ...kmsListing, authorizationRequired: true }),
    }),
    who = owner();
  try {
    assert.ok((await r.controller.capabilities()).includes("kms"));
    const { response, state } = await request(r, who, "kms");
    assert.equal(state.authorizationRequired, true);
    assert.equal(r.runs.length, 0, "listing/selecting KMS does not prompt");
    for (const consent of [undefined, false, "true", 1, {}]) {
      await assert.rejects(
        r.controller.choose(state.id, "kms-monitor-token", consent),
        /Allow direct screen capture/,
      );
    }
    assert.equal(r.runs.length, 0);
    const choosing = r.controller.choose(state.id, "kms-monitor-token", true);
    assert.equal(r.controller.state().authorizationPending, true);
    const source = r.runs[0];
    assert.deepEqual(source.args.slice(-2), ["--authorize", "true"]);
    assert.deepEqual(source.helper.timeouts, [115000]);
    r.controller.cancel(state.id);
    assert.deepEqual(await response, { action: "cancel" });
    await choosing;
    source.helper.events.emit("message", { event: "ready", nodeId: 42 });
    assert.equal(r.controller.state(), null);
    assert.equal(source.helper.stops, 1);
    assert.equal(source.helper.events.listenerCount("message"), 0);
  } finally {
    r.controller.close();
  }
});

test("KMS denied or unavailable authorization stays retryable without an automatic prompt", async () => {
  for (const reason of ["authorization-cancelled", "authorization-unavailable"]) {
    const r = rig({
      platform: "linux",
      list: async () => ({ ...kmsListing, authorizationRequired: true }),
    });
    try {
      const { state } = await request(r, owner(), "kms");
      const choosing = r.controller.choose(state.id, "kms-monitor-token", true);
      r.runs[0].helper.events.emit("message", { event: "error", reason });
      await choosing;
      assert.equal(r.controller.state().phase, "choosing");
      assert.equal(r.controller.state().authorizationPending, false);
      assert.equal(r.controller.state().authorizationRequired, true);
      assert.match(r.controller.state().error, /authorization/);
      assert.equal(r.runs.length, 1);
      assert.equal(r.runs[0].helper.stops, 1);
    } finally {
      r.controller.close();
    }
  }
});

test("direct selection passes only the chosen source and cleans ownership on stop", async () => {
  const r = rig(),
    who = owner();
  try {
    const { response, state } = await request(r, who);
    assert.equal(state.sources[0].name, "TestMonitor");
    const choosing = r.controller.choose(state.id, "output-1");
    await turn();
    const source = r.runs[0];
    assert.deepEqual(source.args, ["--output", "output-1", "--fps", "30"]);
    source.helper.events.emit("message", { event: "ready", nodeId: 42 });
    await choosing;
    assert.deepEqual(await response, {
      action: "selected",
      socketPath: "/run/user/test/pipewire-0",
      nodeId: 42,
      method: "wayland-direct",
      sourceType: "screen",
    });
    assert.equal(r.controller.state(), null);
    r.controller.event(who, { requestId: 1, event: "started" });
    r.controller.rate(who, 60);
    assert.deepEqual(source.helper.sent, [{ op: "rate", fps: 60 }]);
    r.controller.event(who, { requestId: 1, event: "stopped" });
    assert.equal(source.helper.stops, 1);
    assert.deepEqual(who.messages, [["dpe-capture-ended", { requestId: 1, reason: "ended" }]]);
    assert.equal(who.listenerCount("destroyed"), 0);
    assert.equal(who.listenerCount("did-start-navigation"), 0);
    assert.equal(r.window.listenerCount("closed"), 0);
    assert.equal(source.helper.events.listenerCount("message"), 0);
  } finally {
    r.controller.close();
  }
});

test("monitor unplug ends one session once and replug allows a fresh explicit selection", async () => {
  for (const method of ["wayland-direct", "kms"]) {
    const r = rig(method === "kms" ? { platform: "linux", list: async () => kmsListing } : {});
    const who = owner();
    try {
      for (let id = 1; id <= 3; id++) {
        const pending = await request(r, who, method, id);
        const choosing = r.controller.choose(
          pending.state.id,
          method === "kms" ? "kms-monitor-token" : "output-1",
        );
        await turn();
        const source = r.runs.at(-1).helper;
        source.events.emit("message", { event: "ready", nodeId: 40 + id });
        await choosing;
        assert.equal((await pending.response).nodeId, 40 + id);
        r.controller.event(who, { requestId: id, event: "started" });
        source.events.emit("message", { event: "ended", failed: true, errorCode: "source-lost" });
        source.events.emit("failure", new Error("monitor disconnected"));
        source.events.emit("message", { event: "ready", nodeId: 999 });
        r.controller.event(who, { requestId: id, event: "stopped" });
        assert.equal(source.stops, 1);
        assert.equal(source.events.listenerCount("message"), 0);
        assert.equal(who.messages.length, id);
        assert.equal(who.messages.at(-1)[1].requestId, id);
        assert.equal(r.controller.state(), null);
      }
    } finally {
      r.controller.close();
    }
  }
});

test("cancelling during ready wait stops the producer and ignores its late ready event", async () => {
  const r = rig(),
    who = owner();
  try {
    const { response, state } = await request(r, who);
    const choosing = r.controller.choose(state.id, "output-1");
    await turn();
    r.controller.cancel(state.id);
    assert.deepEqual(await response, { action: "cancel" });
    await choosing;
    r.runs[0].helper.events.emit("message", { event: "ready", nodeId: 42 });
    assert.equal(r.controller.state(), null);
    assert.equal(r.runs[0].helper.stops, 1);
    assert.equal(r.runs[0].helper.events.listenerCount("message"), 0);
  } finally {
    r.controller.close();
  }
});

test("direct startup failures stay visible and permit a new source attempt", async () => {
  const r = rig(),
    who = owner();
  try {
    const { response, state } = await request(r, who);
    const choosing = r.controller.choose(state.id, "output-1");
    await turn();
    r.runs[0].helper.events.emit("failure", new Error("GPU import failed"));
    await choosing;
    assert.equal(r.controller.state().phase, "choosing");
    assert.match(r.controller.state().error, /GPU import failed/);
    const retry = r.controller.choose(state.id, "output-1");
    await turn();
    r.runs[1].helper.events.emit("message", { event: "ready", nodeId: 43 });
    await retry;
    assert.equal((await response).nodeId, 43);
  } finally {
    r.controller.close();
  }
});

test("subframe navigation cannot consume the main-frame cleanup listener", async () => {
  const r = rig(),
    who = owner();
  try {
    const { response } = await request(r, who);
    who.emit("did-start-navigation", {}, "https://example.invalid", false, false);
    assert.ok(r.controller.state());
    who.emit("did-start-navigation", {}, "https://example.invalid", false, true);
    assert.deepEqual(await response, { action: "cancel" });
    assert.equal(r.controller.state(), null);
    assert.equal(who.listenerCount("destroyed"), 0);
  } finally {
    r.controller.close();
  }
});

test("same-document routes preserve active capture; full navigation still cleans it up", async () => {
  for (const modern of [false, true]) {
    const r = rig(),
      who = owner();
    try {
      const { response, state } = await request(r, who);
      const choosing = r.controller.choose(state.id, "output-1");
      await turn();
      r.runs[0].helper.events.emit("message", { event: "ready", nodeId: 42 });
      await choosing;
      await response;
      r.controller.event(who, { requestId: 1, event: "started" });
      const navigate = (sameDocument) =>
        modern
          ? who.emit("did-start-navigation", { isMainFrame: true, isSameDocument: sameDocument })
          : who.emit(
              "did-start-navigation",
              {},
              "https://discord.com/channels/test",
              sameDocument,
              true,
            );
      navigate(true);
      navigate(true);
      assert.equal(r.runs[0].helper.stops, 0);
      assert.deepEqual(who.messages, []);
      navigate(false);
      assert.equal(r.runs[0].helper.stops, 1);
      assert.equal(who.listenerCount("did-start-navigation"), 0);
    } finally {
      r.controller.close();
    }
  }
});

test("opening and cancelling a replacement picker preserves capture until its replacement starts", async () => {
  const r = rig(),
    who = owner();
  try {
    const first = await request(r, who);
    let choosing = r.controller.choose(first.state.id, "output-1");
    await turn();
    r.runs[0].helper.events.emit("message", { event: "ready", nodeId: 42 });
    await choosing;
    await first.response;
    r.controller.event(who, { requestId: 1, event: "started" });
    const cancelled = await request(r, who, "wayland-direct", 2);
    assert.equal(r.runs[0].helper.stops, 0);
    r.controller.cancel(cancelled.state.id);
    assert.deepEqual(await cancelled.response, { action: "cancel" });
    assert.equal(r.runs[0].helper.stops, 0);
    const replacement = await request(r, who, "wayland-direct", 3);
    choosing = r.controller.choose(replacement.state.id, "output-1");
    await turn();
    r.runs[1].helper.events.emit("message", { event: "ready", nodeId: 43 });
    await choosing;
    await replacement.response;
    assert.equal(r.runs[0].helper.stops, 0);
    r.controller.event(who, { requestId: 3, event: "started" });
    assert.equal(r.runs[0].helper.stops, 1);
    assert.equal(r.runs[1].helper.stops, 0);
    r.controller.event(who, { requestId: 1, event: "stopped" });
    assert.equal(r.runs[1].helper.stops, 0);
    r.controller.event(who, { requestId: 3, event: "stopped" });
    assert.equal(r.runs[1].helper.stops, 1);
    assert.equal(who.listenerCount("did-start-navigation"), 0);
  } finally {
    r.controller.close();
  }
});

test("owner destruction and controller closure suppress late capability completion", async () => {
  for (const close of [false, true]) {
    const probe = deferred(),
      r = rig({ list: () => probe.promise }),
      who = owner();
    const response = r.controller.request(who, {
      requestId: 1,
      sourceType: 0,
      method: "wayland-direct",
    });
    if (close) r.controller.close();
    else who.destroy();
    probe.resolve({ available: true, outputs: [] });
    assert.deepEqual(await response, { action: "cancel" });
    assert.equal(r.opens(), 0);
    assert.equal(r.controller.state(), null);
    r.controller.close();
  }
});

test("older capability probes cannot replace a newer source selection", async () => {
  const old = deferred();
  let calls = 0;
  const r = rig({
    list: () => (++calls === 1 ? old.promise : Promise.resolve({ available: true, outputs: [] })),
  });
  try {
    const first = r.controller.request(owner(1), {
      requestId: 1,
      sourceType: 0,
      method: "wayland-direct",
    });
    const { response, state } = await request(r, owner(2), "wayland-direct", 2);
    old.resolve({ available: true, outputs: [] });
    assert.deepEqual(await first, { action: "cancel" });
    assert.equal(r.controller.state().id, state.id);
    r.window.emit("closed");
    assert.deepEqual(await response, { action: "cancel" });
  } finally {
    r.controller.close();
  }
});

test("listing or cancelling another game selection cannot stop or retime an active game", async () => {
  const r = rig(),
    first = owner(1),
    second = owner(2);
  try {
    const active = await request(r, first, "vkcapture");
    const choosing = r.controller.choose(active.state.id, "game");
    const broker = r.runs[0].helper;
    broker.events.emit("message", {
      event: "ready",
      token: "game",
      nodeId: 42,
      handoff: "copy",
      handoffFallback: "synchronization-unavailable",
    });
    await choosing;
    const selected = await active.response;
    assert.equal(selected.action, "selected");
    assert.equal(selected.handoff, "copy");
    assert.equal(selected.handoffFallback, "synchronization-unavailable");
    r.controller.event(first, { requestId: 1, event: "started" });
    const pending = await request(r, second, "vkcapture", 2);
    await assert.rejects(r.controller.choose(pending.state.id, "game"), /Another game capture/);
    r.controller.rate(second, 60);
    r.controller.cancel(pending.state.id);
    assert.deepEqual(await pending.response, { action: "cancel" });
    assert.equal(broker.commands.filter((value) => ["stop", "rate"].includes(value.op)).length, 0);
    first.destroy();
    assert.equal(broker.commands.filter((value) => value.op === "stop").length, 1);
    broker.events.emit("message", { event: "stopped", token: "game" });
    assert.equal(first.listenerCount("destroyed"), 0);
  } finally {
    r.controller.close();
  }
});

test("failed game start tears down its helper and permits a fresh source refresh", async () => {
  const r = rig(),
    who = owner();
  try {
    const pending = await request(r, who, "vkcapture");
    r.runs[0].helper.startError = new Error("synthetic start failure");
    await r.controller.choose(pending.state.id, "game");
    assert.equal(r.runs[0].helper.stops, 1);
    assert.match(r.controller.state().error, /synthetic start failure/);
    assert.equal(r.controller.state().phase, "choosing");
    await r.controller.refresh();
    assert.equal(r.runs.length, 2);
    r.controller.cancel(pending.state.id);
    await pending.response;
  } finally {
    r.controller.close();
  }
});

test("game replacement waits for the old producer only after an explicit source choice", async () => {
  const r = rig(),
    who = owner();
  try {
    const active = await request(r, who, "vkcapture", 1);
    let choosing = r.controller.choose(active.state.id, "game");
    const broker = r.runs[0].helper;
    broker.events.emit("message", { event: "ready", token: "game", nodeId: 42 });
    await choosing;
    await active.response;
    r.controller.event(who, { requestId: 1, event: "started" });
    const replacement = await request(r, who, "vkcapture", 2);
    assert.equal(broker.commands.filter((value) => value.op === "stop").length, 0);
    choosing = r.controller.choose(replacement.state.id, "game");
    assert.equal(broker.commands.filter((value) => value.op === "stop").length, 1);
    assert.equal(broker.commands.filter((value) => value.op === "start").length, 1);
    broker.events.emit("message", { event: "stopped", token: "game" });
    await turn();
    assert.equal(broker.commands.filter((value) => value.op === "start").length, 2);
    broker.events.emit("message", { event: "ready", token: "game", nodeId: 43 });
    await choosing;
    assert.equal((await replacement.response).nodeId, 43);
    r.controller.event(who, { requestId: 2, event: "started" });
    r.controller.event(who, { requestId: 1, event: "stopped" });
    assert.equal(broker.commands.filter((value) => value.op === "stop").length, 1);
  } finally {
    r.controller.close();
  }
});

test("acknowledged stop releases a game cancelled while start was succeeding", async () => {
  const r = rig(),
    who = owner();
  try {
    const first = await request(r, who, "vkcapture", 1);
    const starting = r.controller.choose(first.state.id, "game");
    const broker = r.runs[0].helper;
    r.controller.cancel(first.state.id);
    await starting;
    assert.deepEqual(await first.response, { action: "cancel" });
    broker.events.emit("message", { event: "stopped", token: "game" });
    const next = await request(r, who, "vkcapture", 2);
    const choosing = r.controller.choose(next.state.id, "game");
    broker.events.emit("message", { event: "ready", token: "game", nodeId: 43 });
    await choosing;
    assert.equal((await next.response).nodeId, 43);
  } finally {
    r.controller.close();
  }
});

test("ready followed immediately by termination cannot publish a dead node", async () => {
  for (const [method, terminal] of [
    ["wayland-direct", "ended"],
    ["wayland-direct", "failure"],
    ["vkcapture", "ended"],
    ["vkcapture", "stopped"],
  ]) {
    const r = rig(),
      who = owner();
    try {
      const pending = await request(r, who, method);
      const choosing = r.controller.choose(
        pending.state.id,
        method === "vkcapture" ? "game" : "output-1",
      );
      await turn();
      const helper = r.runs[0].helper;
      helper.events.emit("message", { event: "ready", token: "game", nodeId: 42 });
      if (terminal === "failure") helper.events.emit("failure", new Error("capture stopped"));
      else helper.events.emit("message", { event: terminal, token: "game" });
      await choosing;
      assert.equal(r.controller.state()?.phase, "choosing", `${method}:${terminal}`);
      assert.match(r.controller.state().error, /stopped/);
      r.controller.cancel(pending.state.id);
      assert.deepEqual(await pending.response, { action: "cancel" });
    } finally {
      r.controller.close();
    }
  }
});

test("unsupported game buffers report an actionable error without a ready timeout", async () => {
  const r = rig(),
    who = owner();
  try {
    const pending = await request(r, who, "vkcapture");
    const choosing = r.controller.choose(pending.state.id, "game");
    r.runs[0].helper.events.emit("message", {
      event: "ended",
      token: "game",
      errorCode: "unsupported-buffer",
    });
    await choosing;
    assert.match(r.controller.state().error, /SDR mode or the System picker/);
    assert.equal(r.controller.state().phase, "choosing");
    r.controller.cancel(pending.state.id);
    await pending.response;
  } finally {
    r.controller.close();
  }
});

test("worker delivers a fast helper's final permission error before process failure", async () => {
  const source = `require('node:fs').writeSync(1, JSON.stringify({event:'error',reason:'permission-required'})+'\\n');process.exit(1)`;
  const processWorker = worker(process.execPath, ["-e", source]);
  const events = [];
  processWorker.events.on("message", (message) => events.push(message.reason));
  const failed = new Promise((resolve) =>
    processWorker.events.once("failure", () => {
      events.push("failure");
      resolve();
    }),
  );
  try {
    const terminal = await processWorker.wait((message) => message.event === "error");
    assert.equal(terminal.reason, "permission-required");
    await failed;
    assert.deepEqual(events, ["permission-required", "failure"]);
  } finally {
    processWorker.stop();
  }
});

test(
  "helper cleanup kills an owned descendant after the group leader has already exited",
  { skip: process.platform !== "linux", timeout: 6000 },
  async () => {
    const childCode =
      "process.on('SIGTERM',()=>{});process.stdout.write('ready\\n');setInterval(()=>{},1000)";
    const source = `const {spawn}=require('node:child_process');const child=spawn(process.execPath,['-e',${JSON.stringify(childCode)}],{stdio:['ignore','pipe','ignore']});child.stdout.once('data',()=>{process.stdout.write(JSON.stringify({event:'ready',pid:child.pid})+'\\n');setTimeout(()=>process.exit(0),30)});`;
    const processWorker = worker(process.execPath, ["-e", source]);
    const ready = await processWorker.wait((message) => message?.event === "ready");
    const deadline = Date.now() + 4000;
    const alive = () => {
      try {
        return fs.readFileSync(`/proc/${ready.pid}/stat`, "utf8").split(") ")[1][0] !== "Z";
      } catch {
        return false;
      }
    };
    try {
      while (alive() && Date.now() < deadline)
        await new Promise((resolve) => setTimeout(resolve, 40));
      assert.equal(alive(), false);
    } finally {
      processWorker.stop();
      if (alive()) {
        try {
          process.kill(ready.pid, "SIGKILL");
        } catch {
          /* Already reaped. */
        }
      }
    }
  },
);

test("Optimized inventory uses opaque per-owner selections, screens first and scoped permission", async () => {
  let enabled = true,
    allowed = true;
  const r = rig({
    experimental: () => enabled,
    permission: async () => ({ allowed }),
    list: async (file) =>
      file.endsWith("dpe-kms-source")
        ? {
            ...kmsListing,
            outputs: [
              { id: "kms-card1-413", name: "DP-2", supported: true },
              { id: "kms-card1-422", name: "DP-3", supported: false },
            ],
          }
        : { available: false },
  });
  const who = owner();
  try {
    const catalog = await r.controller.catalog(who);
    assert.deepEqual(
      catalog.sources.map((source) => source.name),
      ["Screen 1 (DP-2)", "Screen 2 (DP-3)", "Synthetic game"],
    );
    assert.deepEqual(
      catalog.sources.map((source) => source.kind),
      ["screen", "screen", "window"],
    );
    assert.equal(r.opens(), 0);
    assert.equal(r.runs.length, 1, "only discovery broker, no pixel producer");
    assert.match(catalog.sources[0].token, /^[a-f0-9-]{36}$/);
    assert.throws(() => r.controller.arm(owner(2), catalog.sources[0].token), /Refresh/);
    assert.throws(() => r.controller.arm(who, catalog.sources[1].token), /available/);
    const again = await r.controller.catalog(who);
    assert.equal(
      again.sources[0].token,
      catalog.sources[0].token,
      "refresh preserves source identity and focus",
    );
    allowed = false;
    const restricted = await r.controller.catalog(who);
    assert.equal(
      restricted.sources.some((source) => source.kind === "screen"),
      false,
    );
    assert.throws(() => r.controller.arm(who, catalog.sources[0].token), /available/);
    enabled = false;
    assert.deepEqual(await r.controller.catalog(who), { sources: [], enabled: false });
    assert.throws(() => r.controller.arm(who, catalog.sources[0].token), /disabled/);
  } finally {
    r.controller.close();
  }
});

test("Optimized handoff is one-shot and preserves native requests without opening DPE settings", async () => {
  const r = rig({
    experimental: () => true,
    permission: async () => ({ allowed: true }),
    exists: (file) => file.endsWith("dpe-kms-source"),
    list: async () => kmsListing,
  });
  const who = owner();
  try {
    assert.deepEqual(await r.controller.requestArmed(who, { requestId: 1, sourceType: 0 }), {
      action: "native",
    });
    const catalog = await r.controller.catalog(who);
    r.controller.arm(who, catalog.sources[0].token);
    await r.controller.closePicker(who, true); // UI disappears before the asynchronous native request.
    const selected = r.controller.requestArmed(who, { requestId: 2, sourceType: 0, fps: 60 });
    await turn();
    assert.equal(r.opens(), 0);
    assert.equal(r.runs.length, 1);
    assert.equal(
      r.runs[0].args.includes("--authorize"),
      false,
      "picker cannot request interactive privilege",
    );
    r.runs[0].helper.events.emit("message", { event: "ready", nodeId: 42, handoff: "copy" });
    assert.equal((await selected).action, "selected");
    assert.deepEqual(await r.controller.requestArmed(who, { requestId: 3, sourceType: 0 }), {
      action: "native",
    });
    r.controller.event(who, { requestId: 2, event: "started" });
    r.controller.event(who, { requestId: 2, event: "stopped" });
    assert.equal(r.runs[0].helper.stops, 1);
  } finally {
    r.controller.close();
  }
});

test("occupied and unavailable vkcapture sources are hidden, with transition-only private logs", async () => {
  const logs = [];
  let compatibility = "listening",
    fail = false;
  const r = rig({
    experimental: () => true,
    permission: async () => ({ allowed: true }),
    log: (message) => logs.push(message),
    run() {
      const instance = helper();
      instance.command = async () => {
        if (fail) throw Error("private process details");
        return {
          compatibility,
          sources: [
            { token: "stock", name: "Private game title", protocol: "obs", available: true },
            { token: "bundled", name: "Bundled game", protocol: "dpe", available: true },
            { token: "unavailable", name: "Unavailable title", protocol: "dpe", available: false },
          ],
        };
      };
      return instance;
    },
  });
  const who = owner();
  const games = (catalog) => catalog.sources.filter((source) => source.kind === "window");
  try {
    const initial = await r.controller.catalog(who);
    assert.equal(games(initial).length, 2);
    const stockToken = games(initial)[0].token;
    compatibility = "in-use";
    const occupied = await r.controller.catalog(who);
    assert.deepEqual(
      games(occupied).map((source) => source.name),
      ["Bundled game"],
    );
    assert.equal(
      occupied.sources.some((source) => source.kind === "screen"),
      true,
    );
    assert.throws(() => r.controller.arm(who, stockToken), /available/);
    await r.controller.catalog(who);
    assert.equal(logs.filter((message) => message.includes("another client")).length, 1);
    assert.equal(logs.filter((message) => message.includes("Unavailable vkcapture")).length, 1);
    compatibility = "listening";
    assert.equal(games(await r.controller.catalog(who)).length, 2);
    compatibility = "in-use";
    await r.controller.catalog(who);
    assert.equal(logs.filter((message) => message.includes("another client")).length, 2);
    fail = true;
    assert.equal(games(await r.controller.catalog(who)).length, 0);
    await r.controller.catalog(who);
    assert.equal(logs.filter((message) => message.includes("discovery failed")).length, 1);
    assert.equal(
      logs.some((message) => /Private|Unavailable title|private process/.test(message)),
      false,
    );
  } finally {
    r.controller.close();
  }
});

test("persistent previews reuse source sessions, progress concurrently and stop on picker close", async (t) => {
  t.mock.timers.enable({ apis: ["Date"], now: 1000 });
  const jobs = [],
    signals = [];
  const r = rig({
    experimental: () => true,
    permission: async () => ({ allowed: true }),
    list: async () => ({
      available: true,
      outputs: [1, 2].map((n) => ({ id: `screen-${n}`, name: `Screen ${n}`, supported: true })),
    }),
    thumbnail: async (_file, _args, _env, signal) => {
      signals.push(signal);
      const job = deferred();
      jobs.push(job);
      return job.promise;
    },
    encodeThumbnail: () => ({ url: "data:image/jpeg;base64,AAAA" }),
  });
  try {
    const who = owner();
    const { sources } = await r.controller.catalog(who);
    const request = (n) =>
      r.controller.preview(who, { token: sources[n].token, width: 320, height: 180 });
    const first = request(0),
      second = request(1);
    await turn();
    assert.equal(jobs.length, 2, "independent sources acquire concurrently");
    assert.deepEqual(await request(0), {}, "no second outstanding frame on one source");
    jobs[1].resolve({});
    assert.ok((await second).url, "second source does not wait for first");
    jobs[0].resolve({});
    await first;
    t.mock.timers.tick(35);
    const next = request(0);
    await turn();
    assert.equal(signals[0], signals[2], "same AbortSignal retains the native session");
    await r.controller.closePicker(who);
    assert.ok(signals.every((signal) => signal.aborted));
    jobs[2].resolve({});
    assert.deepEqual(await next, {}, "cancelled frame cannot reach renderer");
  } finally {
    r.controller.close();
  }
});

test("permission disabled hides all monitors even when direct Wayland is supported", async () => {
  const r = rig({ experimental: () => true, permission: async () => ({ allowed: false }) });
  try {
    const result = await r.controller.catalog(owner());
    assert.equal(
      result.sources.some((source) => source.kind === "screen"),
      false,
    );
    assert.equal(
      result.sources.some((source) => source.kind === "window"),
      true,
    );
  } finally {
    r.controller.close();
  }
});

test("game discovery invokes the broker through isolated system Python", async () => {
  const r = rig({ experimental: () => true });
  try {
    const result = await r.controller.catalog(owner());
    assert.ok(result.sources.some((source) => source.kind === "window"));
    assert.equal(r.runs[0].file, "/usr/bin/python3");
    assert.deepEqual(r.runs[0].args.slice(0, 2), [
      "-I",
      "/test/app/capture/vkcapture/vkcapture-broker.py",
    ]);
  } finally {
    r.controller.close();
  }
});

test("read-only packaged broker script can answer discovery without execute permission", async () => {
  const os = require("node:os");
  const path = require("node:path");
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "dpe-broker-python-"));
  const script = path.join(directory, "broker.py");
  fs.writeFileSync(
    script,
    'import json,sys\nfor line in sys.stdin:\n request=json.loads(line)\n print(json.dumps({"id":request["id"],"ok":True,"sources":[]}),flush=True)\n',
    { mode: 0o644 },
  );
  const broker = worker("/usr/bin/python3", ["-I", script]);
  try {
    const result = await broker.command({ op: "list" });
    assert.deepEqual(result.sources, []);
  } finally {
    broker.stop();
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test("monitor numbering follows portable Wayland logical positions, not DRM discovery order", async () => {
  const outputs = [
    { id: "right", name: "DP-3", supported: true },
    { id: "left-bottom", name: "DP-2", supported: true },
    { id: "left-top", name: "HDMI-A-1", supported: true },
  ];
  let layout = [
    { name: "DP-3", position: { x: 1920, y: 0 } },
    { name: "DP-2", position: { x: -1280, y: 1080 } },
    { name: "HDMI-A-1", position: { x: -1280, y: 0 } },
  ];
  const r = rig({
    experimental: () => true,
    list: async (file) =>
      file.endsWith("dpe-kms-source")
        ? { ...kmsListing, outputs }
        : { available: false, outputs: layout },
  });
  try {
    const who = owner();
    const first = (await r.controller.catalog(who)).sources.filter((s) => s.kind === "screen");
    assert.deepEqual(
      first.map((s) => s.name),
      ["Screen 1 (HDMI-A-1)", "Screen 2 (DP-2)", "Screen 3 (DP-3)"],
    );
    layout = [{ name: "DP-2", position: { x: -1280, y: 0 } }];
    const incomplete = (await r.controller.catalog(who)).sources.filter((s) => s.kind === "screen");
    assert.deepEqual(
      incomplete.map((s) => s.name),
      ["Screen 1 (DP-3)", "Screen 2 (DP-2)", "Screen 3 (HDMI-A-1)"],
    );
    assert.equal(
      first[2].token,
      incomplete[0].token,
      "renumbering never changes selected monitor identity",
    );
    layout = [
      { name: "DP-3", position: { x: 1920, y: 0 } },
      { name: "DP-2", position: { x: 0, y: 0 } },
      { name: "HDMI-A-1", position: { x: 0, y: 1080 } },
      { name: "DP-3", position: { x: -500, y: 0 } },
    ];
    assert.deepEqual(
      (await r.controller.catalog(who)).sources
        .filter((s) => s.kind === "screen")
        .map((s) => s.name),
      incomplete.map((s) => s.name),
      "ambiguous connector names cannot reorder a different GPU's output",
    );
  } finally {
    r.controller.close();
  }
});

test("closing a picker during game handoff preserves selection and does not suspend its broker", async () => {
  const r = rig({ experimental: () => true });
  const who = owner();
  try {
    const catalog = await r.controller.catalog(who);
    const game = catalog.sources.find((source) => source.kind === "window");
    assert.ok(game);
    r.controller.arm(who, game.token);
    const broker = r.runs.find((run) =>
      run.args.some((arg) => arg.endsWith("vkcapture-broker.py")),
    ).helper;
    broker.commands.length = 0;
    await r.controller.closePicker(who, true);
    assert.equal(
      broker.commands.some((command) => command.op === "suspend"),
      false,
    );
    const selecting = r.controller.requestArmed(who, { requestId: 77, sourceType: 1, fps: 60 });
    await turn();
    assert.equal(
      broker.commands.some((command) => command.op === "start"),
      true,
    );
    broker.events.emit("message", { event: "ready", nodeId: 42, token: "game" });
    assert.equal((await selecting).action, "selected");
  } finally {
    r.controller.close();
  }
});
test("ordinary picker close disarms a previously selected source", async () => {
  const r = rig({
    experimental: () => true,
    permission: async () => ({ allowed: true }),
    exists: (file) => file.endsWith("dpe-kms-source"),
    list: async () => kmsListing,
  });
  const who = owner();
  try {
    const catalog = await r.controller.catalog(who);
    r.controller.arm(who, catalog.sources[0].token);
    await r.controller.closePicker(who);
    assert.deepEqual(await r.controller.requestArmed(who, { requestId: 78, sourceType: 0 }), {
      action: "native",
    });
  } finally {
    r.controller.close();
  }
});

test("game start reply carries authenticated process identity into selection", async () => {
  const r = rig();
  const who = owner();
  try {
    const pending = await request(r, who, "vkcapture");
    const broker = r.runs[0].helper;
    const command = broker.command.bind(broker);
    const identity = { pid: 200, start: "2000" };
    broker.command = async (value) => ({ ...(await command(value)), processIdentity: identity });
    const choosing = r.controller.choose(pending.state.id, "game");
    await turn();
    broker.events.emit("message", { event: "ready", token: "game", nodeId: 12 });
    await choosing;
    const selected = await pending.response;
    assert.equal(selected.processIdentity, identity);
  } finally {
    r.controller.close();
  }
});

test("reviewed KMS direct capture selects the descriptor endpoint and closes its helper", async () => {
  const r = rig({ platform: "linux", list: async () => kmsListing }),
    who = owner();
  try {
    const response = r.controller.request(who, {
      requestId: 1,
      sourceType: 0,
      method: "kms",
      fps: 60,
      directGpu: true,
    });
    await turn();
    const choosing = r.controller.choose(r.controller.state().id, "kms-monitor-token");
    const source = r.runs[0];
    assert.equal(source.file, "/test/app/capture/dpe-direct-source");
    source.helper.events.emit("message", {
      event: "ready",
      nodeId: 0xfffffffe,
      transport: "direct-gpu",
      handoff: "direct-gpu",
      socketPath: "/run/user/test/dpe-direct-private/frames",
    });
    await choosing;
    const selected = await response;
    assert.equal(selected.transport, "direct-gpu");
    assert.equal(selected.handoff, "direct-gpu");
    assert.equal(selected.socketPath, "/run/user/test/dpe-direct-private/frames");
    assert.equal(selected.nodeId, 0xfffffffe);
    r.controller.event(who, { event: "cancelled", requestId: 1 });
    assert.equal(source.helper.stops, 1);
  } finally {
    r.controller.close();
  }
});

test("a direct GPU request rejects an unexpected PipeWire producer result", async () => {
  const r = rig({ platform: "linux", list: async () => kmsListing }),
    who = owner();
  try {
    void r.controller.request(who, {
      requestId: 1,
      sourceType: 0,
      method: "kms",
      fps: 60,
      directGpu: true,
    });
    await turn();
    const choosing = r.controller.choose(r.controller.state().id, "kms-monitor-token");
    r.runs[0].helper.events.emit("message", { event: "ready", nodeId: 42 });
    await choosing;
    assert.match(r.controller.state().error, /did not establish direct GPU transport/);
    assert.equal(r.runs[0].helper.stops, 1);
  } finally {
    r.controller.close();
  }
});

test("stock OBS compatibility keeps its explicit PipeWire path with a direct-capable consumer", async () => {
  const broker = helper(),
    original = broker.command;
  broker.command = async function (value) {
    if (value.op === "list")
      return { sources: [{ token: "game", name: "Stock hook", protocol: "obs", available: true }] };
    return original.call(this, value);
  };
  const r = rig({ run: () => broker }),
    who = owner();
  try {
    const response = r.controller.request(who, {
      requestId: 1,
      sourceType: 0,
      method: "vkcapture",
      fps: 60,
      directGpu: true,
    });
    await turn();
    const choosing = r.controller.choose(r.controller.state().id, "game");
    await turn();
    assert.equal(broker.commands.find((value) => value.op === "start").transport, undefined);
    broker.events.emit("message", { event: "ready", token: "game", nodeId: 42 });
    await choosing;
    assert.equal((await response).transport, undefined);
  } finally {
    r.controller.close();
  }
});
