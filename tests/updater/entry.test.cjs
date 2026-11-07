const assert = require("node:assert/strict");
const { test } = require("node:test");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function fixture({
  pending = true,
  existingProfile = false,
  lock = true,
  activeWindow = () => undefined,
} = {}) {
  const calls = [];
  const app = Object.assign(new EventEmitter(), {
    setAppPath: () => {},
    relaunch: () => {},
    requestSingleInstanceLock: () => lock,
    whenReady: () => Promise.resolve(),
    exit: (code) => calls.push(["exit", code]),
  });
  function requireModule(name) {
    if (name === "./diagnostic-log.cjs") return { install() {} };
    if (name === "electron")
      return { app, dialog: { showErrorBox: (...args) => calls.push(["error", ...args]) } };
    if (name === "node:path") return path;
    if (name === "node:module") return { globalPaths: [], prototype: { _compile() {} } };
    if (name === "node:fs")
      return {
        readFileSync: (file) =>
          JSON.stringify(
            file.endsWith("client.json")
              ? { modules: { discord_desktop_core: { version: 1 } } }
              : { releaseChannel: "canary", version: "1.0.1" },
          ),
        existsSync: () => existingProfile,
        realpathSync: (value) => value,
      };
    if (name === "./desktop.cjs" || name === "./application-ready.cjs") return () => {};
    if (name === "./patches.cjs") return {};
    if (name === "./capture-backends.cjs")
      return { register: () => calls.push(["capture-backends"]) };
    if (name === "../desktop/preferences.cjs")
      return {
        install: () => calls.push(["desktop-preferences"]),
        configureMediaControls: () => calls.push(["media-controls"]),
      };
    if (name === "./tray.cjs")
      return { setDefaultDirectory: (directory) => calls.push(["tray-assets", directory]) };
    if (name === "../desktop/spellcheck.cjs") return { install: () => calls.push(["spellcheck"]) };
    if (name === "./profile.cjs")
      return { paths: () => ({ init: () => calls.push(["paths"]) }), config: () => ({}) };
    if (name === "./mod-settings.cjs")
      return {
        needsOnboarding: (options) => {
          calls.push(["decision", options.existingProfile]);
          return pending;
        },
        onboard: async () => {
          calls.push(["welcome"]);
          return {};
        },
        activeWindow,
      };
    if (["./window.cjs", "./splash.cjs", "./config.cjs"].includes(name)) return {};
    if (name === "./module-updater.cjs") return () => ({});
    if (name === "/app/bootstrap/src") {
      calls.push(["discord-bootstrap"]);
      return {};
    }
    throw new Error("Unexpected dependency: " + name);
  }
  requireModule.cache = {};
  vm.runInNewContext(fs.readFileSync("integration/updater/entry.cjs", "utf8"), {
    require: requireModule,
    global: {},
    console,
    setTimeout,
    process: {
      platform: "linux",
      env: {
        DPE_APP_ROOT: "/app",
        DPE_CLIENT_HOST: "/host",
        DPE_CLIENT_ROOT: "/client",
        DPE_PROFILE_DIR: "/profile",
        DPE_LAUNCHER: "/launcher",
      },
    },
  });
  Object.defineProperty(calls, "app", { value: app });
  return calls;
}

test("pending first launch never evaluates Discord before Welcome completion", async () => {
  for (const existingProfile of [false, true]) {
    const calls = fixture({ existingProfile });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(calls[0][0], "media-controls");
    assert.ok(calls.some(([name]) => name === "welcome"));
    assert.deepEqual(
      calls.find(([name]) => name === "tray-assets"),
      [
        "tray-assets",
        "/profile/app-1.0.1/modules/discord_desktop_core-1/discord_desktop_core/app/images/systemtray/linux",
      ],
    );
    assert.equal(
      calls.some(([name]) => name === "discord-bootstrap"),
      false,
    );
    assert.equal(
      calls.some(([name]) => name === "error"),
      false,
    );
  }
});

test("completed or existing profiles bootstrap synchronously without a Welcome detour", () => {
  const calls = fixture({ pending: false, existingProfile: true });
  assert.ok(calls.some(([name]) => name === "discord-bootstrap"));
  assert.ok(
    calls.findIndex(([name]) => name === "spellcheck") <
      calls.findIndex(([name]) => name === "discord-bootstrap"),
  );
  assert.equal(
    calls.some(([name]) => name === "welcome"),
    false,
  );
});

test("another first-launch process cannot open a duplicate Welcome", async () => {
  const calls = fixture({ lock: false });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls.at(-1), ["exit", 0]);
  assert.equal(
    calls.some(([name]) => name === "welcome" || name === "discord-bootstrap"),
    false,
  );
});

test("second launch focuses the live onboarding owner after Welcome is replaced", async () => {
  let active;
  const focused = [];
  const calls = fixture({ activeWindow: () => active });
  await new Promise((resolve) => setImmediate(resolve));
  const owner = (name) => ({ isVisible: () => true, show: () => focused.push(name), focus() {} });
  active = owner("welcome");
  calls.app.emit("second-instance");
  active = owner("settings");
  calls.app.emit("second-instance");
  active = undefined;
  calls.app.emit("second-instance");
  assert.deepEqual(focused, ["welcome", "settings"]);
});
