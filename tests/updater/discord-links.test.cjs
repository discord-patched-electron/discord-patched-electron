const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const { createQueue } = require("../../integration/desktop/links.cjs");
const { bootstrap } = require("../../integration/updater/patches.cjs");
const { createController } = require("../../integration/desktop/preferences.cjs");

test("cold and early second-instance links wait for the ready core and drain once", () => {
  const queue = createQueue(["electron", "--url", "--", "discord://invite/first"]);
  const opened = [];
  const core = { handleOpenUrl: (url) => opened.push(url) };
  queue.receive(["--flag", "DISCORD://channels/second"]);
  queue.dispatch(core);
  assert.deepEqual(opened, []);
  queue.start(core);
  queue.dispatch(core);
  assert.deepEqual(opened, ["discord://invite/first", "discord://channels/second"]);
  queue.receive(["discord://channels/third"]);
  queue.dispatch(core);
  assert.equal(opened.at(-1), "discord://channels/third");
});

test("external input rejects invalid schemes, controls and oversized values, with a bounded queue", () => {
  const queue = createQueue([
    null,
    {},
    "https://discord.com",
    "discord:",
    "discord://",
    "discord://bad\nargument",
    "discord://" + "a".repeat(8192),
  ]);
  queue.receive(undefined);
  queue.receive(Array.from({ length: 32 }, (_, i) => `discord://channels/${i}`));
  const opened = [];
  queue.start({ handleOpenUrl: (url) => opened.push(url) });
  assert.equal(opened.length, 16);
  assert.equal(opened[0], "discord://channels/0");
});

test("Welcome handoff preserves pending links, but updates never replay delivered URLs", () => {
  const args = ["--flag", "--url", "--", "discord://invite/first"];
  const queue = createQueue(args);
  queue.receive(["discord://invite/second"]);
  assert.deepEqual(queue.relaunchArguments(args), [
    "--flag",
    "discord://invite/first",
    "discord://invite/second",
  ]);
  queue.start({ handleOpenUrl() {} });
  assert.deepEqual(queue.relaunchArguments(args), ["--flag"]);
});

test("the transformed pinned bootstrap dispatches real cold/early/live arguments", () => {
  const source =
    "const startMin = process.argv?.includes?.('--start-minimized');\nlet desktopCore;\n  app.on('browser-window-created', customize);\ncustomization();\n  desktopCore = require('discord_desktop_core');\napp.on('second-instance', (e,a) => { desktopCore?.handleOpenUrl?.(a.includes('--url') && a[a.indexOf('--') + 1]); });\nfunction ready(){ desktopCore.setMainWindowVisible(!startMin); }";
  const queue = createQueue(["electron", "discord://invite/cold"]);
  const opened = [],
    handlers = new Map();
  const core = { handleOpenUrl: (url) => opened.push(url), setMainWindowVisible() {} };
  const context = {
    splash: {},
    oaConfig: {},
    app: { on: (name, fn) => handlers.set(name, fn) },
    process: { env: { DPE_APP_ROOT: "/app" } },
    require: (name) =>
      name.endsWith("preferences.cjs")
        ? { startMinimized: () => false }
        : name.endsWith("links.cjs")
          ? queue
          : name === "discord_desktop_core"
            ? core
            : () => {},
  };
  vm.runInNewContext(bootstrap(source, "bootstrap.js", "/desktop-exec.cjs"), context);
  handlers.get("second-instance")({}, ["discord://invite/early"]);
  assert.deepEqual(opened, []);
  context.ready();
  handlers.get("second-instance")({}, ["--url", "--", "discord://invite/live"]);
  assert.deepEqual(opened, [
    "discord://invite/cold",
    "discord://invite/early",
    "discord://invite/live",
  ]);
});

test("claiming requires an explicit action, verifies success and respects a later OS choice", () => {
  let current = false,
    claims = 0,
    succeed = true;
  const linkRegistration = {
    current: () => current,
    claim: () => {
      if (current) return;
      claims++;
      current = succeed;
      if (!succeed) throw new Error("Could not make this app open Discord links.");
    },
  };
  const controller = createController({}, () => ({}), { linkRegistration });
  assert.equal(claims, 0);
  controller.claimLinks();
  assert.equal(controller.linksDefault(), true);
  controller.claimLinks();
  assert.equal(claims, 1);
  current = false;
  assert.equal(controller.linksDefault(), false);
  succeed = false;
  assert.throws(() => controller.claimLinks(), /Could not make/);
});
