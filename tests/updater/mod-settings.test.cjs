const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const { PassThrough } = require("node:stream");
const { test } = require("node:test");
const vm = require("node:vm");
const source = fs.readFileSync("integration/updater/mod-settings.cjs", "utf8");

function fixture({ installed = "none", probe } = {}) {
  const app = new EventEmitter();
  let marker = {},
    quits = 0,
    settingsOpen = false;
  app.quit = () => {
    quits++;
  };
  const health = [];
  let configured, api;
  let allowDiscard = true;
  const handlers = new Map();
  const children = [];
  const opened = [];
  const messages = [];
  function makeWindow() {
    const window = new EventEmitter();
    let destroyed = false,
      visible = false;
    window.isDestroyed = () => destroyed;
    window.isVisible = () => visible;
    window.show = () => {
      visible = true;
      window.emit("show");
    };
    window.setParentWindow = (parent) => {
      window.parent = parent;
    };
    window.destroy = () => {
      if (!destroyed) {
        destroyed = true;
        window.emit("closed");
      }
    };
    window.close = () => {
      const event = {
        defaultPrevented: false,
        preventDefault() {
          this.defaultPrevented = true;
        },
      };
      window.emit("close", event);
      if (!event.defaultPrevented) window.destroy();
    };
    window.webContents = Object.assign(new EventEmitter(), {
      mainFrame: {},
      ipc: { handle: (name, callback) => handlers.set(name, callback) },
      send: (...args) => messages.push(args),
    });
    return window;
  }
  const window = makeWindow();
  const context = {
    require(name) {
      if (name === "./diagnostic-log.cjs") return { record() {}, install() {}, capture() {} };
      if (name === "node:path") return path;
      if (name === "node:fs")
        return {
          readFileSync: () => '{"releaseChannel":"canary"}',
          writeFileSync: (...args) => health.push(args),
        };
      if (name === "./profile.cjs") return { readObject: () => marker };
      if (name === "electron") return { app };
      if (name === "./launch-settings.cjs")
        return { confirmClose: () => allowDiscard, resetCloseApproval() {} };
      if (name === "./config.cjs")
        return {
          open: (tab, _source, options) => {
            opened.push(tab);
            if (tab === "setup") return window;
            settingsOpen = true;
            assert.equal(options.parent, undefined);
            assert.equal(options.independent, true);
            assert.equal(options.onboarding, true);
            configured = makeWindow();
            configured.parent = null;
            configured.once("closed", () => {
              settingsOpen = false;
            });
            return configured;
          },
          settingsOpen: () => settingsOpen,
        };
      if (name === "node:child_process")
        return {
          spawnSync(command, args, options) {
            assert.equal(command, "python3");
            assert.deepEqual(Array.from(args), [
              "/app/integration/updater/mod_manager.py",
              "status",
              "/profile",
              "canary",
            ]);
            assert.equal(options.timeout, 5000);
            assert.equal(options.maxBuffer, 64 * 1024);
            assert.equal(options.env.LD_PRELOAD, "");
            return probe || { status: 0, stdout: JSON.stringify({ result: { installed } }) };
          },
          spawn(command, args, options) {
            assert.equal(command, "python3");
            assert.equal(options.detached, true);
            assert.equal(options.env.LD_PRELOAD, "");
            const child = new EventEmitter();
            child.stdout = new PassThrough();
            child.action = args[1];
            child.choice = args[4];
            children.push(child);
            return child;
          },
        };
      throw new Error("Unexpected dependency " + name);
    },
    __dirname: "/app/integration/updater",
    process: { env: { DPE_APP_ROOT: "/app", DPE_PROFILE_DIR: "/profile" } },
    module: { exports: {} },
    setTimeout,
    clearTimeout,
    console,
  };
  vm.runInNewContext(source, context);
  api = context.module.exports;
  api.attach(window);
  const invoke = (name, ...args) =>
    handlers.get("dpe-mod-" + name)(
      { sender: window.webContents, senderFrame: window.webContents.mainFrame },
      ...args,
    );
  const finish = (result, child = children.at(-1)) => {
    child.stdout.end(JSON.stringify({ result }) + "\n");
    child.emit("close", 0);
  };
  return {
    api,
    app,
    window,
    handlers,
    children,
    invoke,
    finish,
    opened,
    messages,
    get configured() {
      return configured;
    },
    set allowDiscard(value) {
      allowDiscard = value;
    },
    health,
    get quits() {
      return quits;
    },
    set marker(value) {
      marker = value;
    },
    env: context.process.env,
  };
}
const state = { installed: "none", setupComplete: false, revision: "old" };
const tick = () => new Promise((resolve) => setImmediate(resolve));

test("mod IPC rejects subframes, other windows, and unsupported choices", async () => {
  const f = fixture();
  await assert.rejects(
    f.handlers.get("dpe-mod-load")({ sender: {}, senderFrame: {} }),
    /local settings window/,
  );
  await assert.rejects(
    f.handlers.get("dpe-mod-load")({ sender: f.window.webContents, senderFrame: {} }),
    /local settings window/,
  );
  assert.match((await f.invoke("apply", "https://untrusted.invalid")).error, /Choose Vencord/);
  assert.equal(f.children.length, 0);
});

test("install progress, busy protection, and restart state survive panel reloads", async () => {
  const f = fixture();
  const pending = f.invoke("apply", "vencord");
  assert.match((await f.invoke("apply", "none")).error, /Wait for mod setup/);
  assert.match((await f.invoke("complete")).error, /Wait for mod setup/);
  let prevented = false;
  f.window.emit("close", {
    preventDefault: () => {
      prevented = true;
    },
  });
  assert.equal(prevented, true);
  f.finish(state);
  await tick();
  assert.equal(f.children.at(-1).choice, "vencord");
  f.children.at(-1).stdout.write('{"progress":{"label":"Installing Vencord…"}}\n');
  f.finish({ ...state, installed: "vencord", revision: "new", setupComplete: true });
  const result = await pending;
  assert.equal(result.busy, false);
  assert.equal(result.restartRequired, true);
  assert.equal(f.messages[0][0], "dpe-mod-progress");
  assert.equal(f.messages.at(-1)[1].label, "Installing Vencord…");
  const loaded = f.invoke("load");
  f.finish({ ...state, installed: "vencord" });
  assert.equal((await loaded).restartRequired, true);
  assert.equal(f.app.listenerCount("will-quit"), 0);
});

test("failed installation releases busy state and does not request restart", async () => {
  const f = fixture();
  const pending = f.invoke("apply", "equicord");
  f.finish(state);
  await tick();
  f.finish({ error: "Download failed" });
  const result = await pending;
  assert.equal(result.error, "Download failed");
  assert.equal(result.busy, false);
  assert.equal(result.restartRequired, false);
});

test("pending Welcome survives a settings file created before cancellation", () => {
  const f = fixture();
  assert.equal(f.api.needsOnboarding(), true);
  assert.equal(f.api.needsOnboarding({ existingProfile: true }), true);
  f.marker = { setupComplete: true };
  assert.equal(f.api.needsOnboarding(), false);
  f.marker = { setupComplete: false, onboardingPending: true };
  assert.equal(f.api.needsOnboarding({ existingProfile: true }), true);
});

test("upgrades offer unseen Welcome only when the current loader has no mod", () => {
  for (const installed of ["none", "vencord", "equicord"]) {
    const f = fixture({ installed });
    assert.equal(f.api.needsOnboarding({ existingProfile: true }), installed === "none");
    f.marker = { setupComplete: true };
    assert.equal(f.api.needsOnboarding({ existingProfile: true }), false);
    f.marker = { onboardingPending: true };
    assert.equal(f.api.needsOnboarding({ existingProfile: true }), true);
  }
  for (const probe of [
    { error: new Error("timeout") },
    { status: 1 },
    { status: 0, stdout: "invalid JSON" },
    { status: 0, stdout: JSON.stringify({ result: { error: "Unreadable installation" } }) },
    { status: 0, stdout: JSON.stringify({ result: { installed: "unknown" } }) },
  ])
    assert.equal(fixture({ probe }).api.needsOnboarding({ existingProfile: true }), false);
});

test("Welcome Next keeps launch gated until Restart to finish persists completion", async () => {
  const f = fixture();
  let launches = 0;
  const pending = f.api.onboard({
    completeLaunch: () => {
      launches++;
    },
  });
  assert.equal(f.children.at(-1).action, "begin");
  f.finish({ ...state, onboardingPending: true });
  await pending;
  const skip = f.invoke("complete");
  assert.equal(f.children.at(-1).action, "status");
  f.finish({ ...state, onboardingPending: true });
  await skip;
  assert.equal(launches, 0);
  const done = f.api.setupAction("done");
  assert.equal(f.children.at(-1).action, "complete");
  await assert.rejects(f.api.setupAction("done"), /Wait for mod setup/);
  await assert.rejects(f.api.setupAction("configure"), /Wait for mod setup/);
  assert.equal(launches, 0);
  f.finish({ ...state, setupComplete: true, onboardingPending: false });
  assert.equal((await done).completed, true);
  assert.equal(launches, 1);
  await assert.rejects(f.api.setupAction("done"), /Welcome is not active/);
  f.window.close();
  assert.equal(f.window.isDestroyed(), true);
  assert.equal(f.quits, 0);
});

test("Configure more closes Welcome only after Settings is ready, then closes on successful completion", async () => {
  const f = fixture();
  let launches = 0;
  const pending = f.api.onboard({
    completeLaunch: () => {
      launches++;
    },
  });
  f.finish({ ...state, onboardingPending: true });
  await pending;
  const configure = f.api.setupAction("configure");
  const duplicate = f.api.setupAction("configure");
  assert.deepEqual(f.opened, ["setup", "settings"]);
  assert.equal(f.window.isDestroyed(), false);
  assert.equal(f.api.isOnboardingSettings(f.configured), false);
  await assert.rejects(f.api.setupAction("done"), /Close Settings/);
  f.configured.show();
  assert.equal((await configure).configuring, true);
  assert.equal((await duplicate).configuring, true);
  assert.equal(f.configured.parent, null);
  assert.equal(f.window.isDestroyed(), true);
  assert.equal(f.api.isOnboardingSettings(f.configured), true);
  assert.equal(f.quits, 0);
  f.configured.close();
  assert.equal(f.configured.isDestroyed(), false);
  assert.equal(f.children.at(-1).action, "complete");
  f.configured.close();
  assert.equal(f.children.filter((child) => child.action === "complete").length, 1);
  f.finish({ ...state, setupComplete: true, onboardingPending: false });
  await tick();
  assert.equal(f.configured.isDestroyed(), true);
  assert.equal(launches, 1);
  assert.equal(f.api.isOnboarding(), false);
});

test("failed or closed loading Settings leaves Welcome retryable without stale ownership", async () => {
  const f = fixture();
  const pending = f.api.onboard({
    completeLaunch: () => {
      throw Error("Unexpected launch");
    },
  });
  f.finish({ ...state, onboardingPending: true });
  await pending;
  for (const failure of ["closed", "failed"]) {
    const configure = f.api.setupAction("configure");
    const rejected = assert.rejects(configure, /Try Configure more again/);
    const child = f.configured;
    if (failure === "closed") child.close();
    else child.webContents.emit("did-fail-load", {}, -2, "failed", "", true);
    await rejected;
    assert.equal(f.window.isDestroyed(), false);
    assert.equal(f.api.isOnboardingSettings(child), false);
    child.emit("show");
    assert.equal(f.window.isDestroyed(), false);
  }
  const retried = f.api.setupAction("configure");
  f.configured.show();
  await retried;
  assert.equal(f.window.isDestroyed(), true);
});

test("unsaved flags can cancel onboarding Settings close without starting completion", async () => {
  const f = fixture();
  const pending = f.api.onboard({ completeLaunch: () => {} });
  f.finish({ ...state, onboardingPending: true });
  await pending;
  const configure = f.api.setupAction("configure");
  f.configured.show();
  await configure;
  f.allowDiscard = false;
  f.configured.close();
  assert.equal(f.configured.isDestroyed(), false);
  assert.equal(f.children.filter((child) => child.action === "complete").length, 0);
  f.allowDiscard = true;
  const completed = f.api.finishSettings(f.configured);
  f.finish({ ...state, setupComplete: true, onboardingPending: false });
  await completed;
  assert.equal(f.configured.isDestroyed(), true);
});

test("cancelling Welcome detaches child-close completion, while ordinary Settings never completes", async () => {
  const f = fixture();
  let launches = 0;
  const pending = f.api.onboard({
    completeLaunch: () => {
      launches++;
    },
  });
  f.finish({ ...state, onboardingPending: true });
  await pending;
  const ordinary = new EventEmitter();
  ordinary.webContents = f.window.webContents;
  f.api.attach(ordinary);
  ordinary.emit("closed");
  assert.equal(f.children.filter((child) => child.action === "complete").length, 0);
  const configure = f.api.setupAction("configure");
  f.window.close();
  assert.equal(f.configured.isDestroyed(), true);
  assert.equal((await configure).cancelled, true);
  assert.equal(f.children.filter((child) => child.action === "complete").length, 1);
  f.finish({ ...state, setupComplete: true, onboardingPending: false });
  await new Promise(setImmediate);
  assert.equal(launches, 1);
  assert.equal(f.quits, 0);
});

test("closing Welcome launches Discord, and healthy rendering acknowledges a trial", async () => {
  const f = fixture();
  let launches = 0;
  f.env.DPE_APPLICATION_READY = "/trial/ready";
  const pending = f.api.onboard({
    completeLaunch: () => {
      launches++;
    },
  });
  f.finish({ ...state, onboardingPending: true });
  await pending;
  assert.equal(f.health.length, 0);
  f.window.emit("show");
  assert.equal(f.health[0][0], "/trial/ready");
  f.window.close();
  assert.equal(f.window.isDestroyed(), false);
  assert.equal(f.quits, 0);
  assert.equal(f.children.filter((c) => c.action === "complete").length, 1);
  f.finish({ ...state, setupComplete: true, onboardingPending: false });
  await new Promise(setImmediate);
  assert.equal(launches, 1);
});

test("Welcome X waits for completion and remains retryable after a save failure", async () => {
  const f = fixture();
  let launches = 0;
  const opened = f.api.onboard({ completeLaunch: () => launches++ });
  f.finish({ ...state, onboardingPending: true });
  await opened;
  f.window.close();
  f.window.close();
  assert.equal(f.children.filter((child) => child.action === "complete").length, 1);
  assert.equal(f.window.isDestroyed(), false);
  f.finish({ error: "Cannot save setup" });
  await tick();
  assert.equal(launches, 0);
  assert.equal(f.api.isOnboarding(), true);
  f.window.close();
  f.finish({ ...state, setupComplete: true, onboardingPending: false });
  await tick();
  assert.equal(launches, 1);
});

for (const choice of ["vencord", "equicord"]) {
  test(`Welcome ${choice} installation stays pending until explicit Next and restart`, async () => {
    const f = fixture();
    let launches = 0;
    const pending = f.api.onboard({
      completeLaunch: () => {
        launches++;
      },
    });
    f.finish({ ...state, onboardingPending: true });
    await pending;
    const install = f.invoke("apply", choice);
    f.finish(state);
    await tick();
    await assert.rejects(f.api.setupAction("done"), /Wait for mod setup/);
    const installed = { ...state, installed: choice, revision: "new", onboardingPending: true };
    f.finish(installed);
    const result = await install;
    assert.equal(result.completed, undefined);
    assert.equal(result.setupComplete, false);
    assert.equal(result.onboardingPending, true);
    assert.equal(result.installed, choice);
    assert.equal(result.restartRequired, false);
    assert.equal(launches, 0);
    assert.equal(f.api.isOnboarding(), true);
    assert.equal(f.children.filter((child) => child.action === "complete").length, 0);
    const next = f.invoke("complete");
    assert.equal(f.children.at(-1).action, "status");
    f.finish(installed);
    await next;
    assert.equal(launches, 0);
    const done = f.api.setupAction("done");
    f.finish({ ...installed, setupComplete: true, onboardingPending: false });
    await done;
    assert.equal(launches, 1);
    assert.equal(f.api.isOnboarding(), false);
    assert.equal(f.messages.at(-1)[1].label, "Restarting Discord…");
  });
}

test("failed Welcome installation and uninstall keep setup open without restarting", async () => {
  const f = fixture();
  let launches = 0;
  const pending = f.api.onboard({
    completeLaunch: () => {
      launches++;
    },
  });
  f.finish({ ...state, onboardingPending: true });
  await pending;
  const install = f.invoke("apply", "vencord");
  f.finish(state);
  await tick();
  f.finish({ error: "Download failed" });
  assert.match((await install).error, /Download failed/);
  assert.equal(f.api.isOnboarding(), true);
  assert.equal(launches, 0);
  const uninstall = f.invoke("apply", "none");
  f.finish({ ...state, installed: "vencord" });
  await tick();
  f.finish({ ...state, onboardingPending: true });
  assert.equal((await uninstall).installed, "none");
  assert.equal(f.children.filter((child) => child.action === "complete").length, 0);
  assert.equal(f.api.isOnboarding(), true);
  assert.equal(launches, 0);
});

for (const failure of ["completion", "restart"]) {
  test(`failed child-close ${failure} preserves the installed mod and permits explicit retry`, async () => {
    const f = fixture();
    let launches = 0;
    let failRestart = failure === "restart";
    const pending = f.api.onboard({
      completeLaunch: () => {
        if (failRestart) {
          failRestart = false;
          throw Error("Could not restart Discord");
        }
        launches++;
      },
    });
    const installed = { ...state, installed: "vencord", onboardingPending: true };
    f.finish(installed);
    await pending;
    const configure = f.api.setupAction("configure");
    f.configured.show();
    await configure;
    f.configured.close();
    f.finish(
      failure === "completion"
        ? { error: "Could not save setup marker" }
        : { ...installed, setupComplete: true, onboardingPending: false },
    );
    await tick();
    assert.equal(f.configured.isDestroyed(), false);
    assert.equal(f.window.isDestroyed(), true);
    assert.equal(f.messages.at(-1)[0], "dpe-setup-error");
    assert.match(f.messages.at(-1)[1], /Could not/);
    assert.equal(f.api.installedInfo().installed, "vencord");
    assert.equal(f.api.isOnboarding(), true);
    assert.equal(launches, 0);
    const done = f.api.setupAction("done");
    f.finish({ ...installed, setupComplete: true, onboardingPending: false });
    assert.equal((await done).completed, true);
    assert.equal(launches, 1);
  });
}

test("download progress carries known byte totals and leaves unknown stages indeterminate", async () => {
  const f = fixture();
  const pending = f.invoke("apply", "vencord");
  f.finish(state);
  await tick();
  f.children
    .at(-1)
    .stdout.write(
      '{"type":"progress","stage":"download","label":"Downloading","current":10,"total":100}\n',
    );
  assert.equal(f.messages.at(-1)[1].current, 10);
  assert.equal(f.messages.at(-1)[1].total, 100);
  f.children
    .at(-1)
    .stdout.write('{"progress":{"stage":"install","label":"Installing","percent":50}}\n');
  assert.equal(f.messages.at(-1)[1].total, undefined);
  assert.equal(f.messages.at(-1)[1].percent, undefined);
  f.finish({ ...state, installed: "vencord" });
  await pending;
});
