const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../../integration/camera/flip.cjs"), "utf8");

function fixture({ supported = true, saved = false } = {}) {
  const callbacks = new Map(),
    sent = [],
    changes = [];
  let refresh;
  const pending = new Promise((resolve) => {
    refresh = resolve;
  });
  const ipcRenderer = {
    on: (name, fn) => callbacks.set(name, fn),
    send: (...args) => sent.push(args),
    invoke: () => pending,
  };
  const module = { exports: {} };
  vm.runInNewContext(source, {
    module,
    console: { error() {} },
    process: {
      env: { DPE_APP_ROOT: "/app", DPE_PROFILE_DIR: "/profile" },
      dlopen: (addon) => {
        addon.exports = { install() {}, setEnabled: (value) => changes.push(value) };
      },
    },
    require: (name) =>
      name === "electron"
        ? { ipcRenderer }
        : name === "node:fs"
          ? {
              readFileSync: (filename) =>
                filename.endsWith("camera-flip.json")
                  ? JSON.stringify({ supported, call: 1, converter: 2, voice_sha256: "hash" })
                  : filename.endsWith("settings.json")
                    ? JSON.stringify({ openasar: { desktop: { webcamFlip: saved } } })
                    : "native",
            }
          : name === "node:crypto"
            ? { createHash: () => ({ update: () => ({ digest: () => "hash" }) }) }
            : require(name),
  });
  return { api: module.exports, callbacks, sent, changes, refresh };
}

test("camera bridge loads saved orientation and live changes win over a delayed startup response", async () => {
  const f = fixture({ saved: true });
  f.api.install("/voice");
  f.api.install("/voice");
  assert.deepEqual(f.changes, [true]);
  f.callbacks.get("dpe-camera-flip")({}, false);
  f.refresh(true);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(f.changes, [true, false]);
  assert.equal(f.sent.at(-1)[1].active, false);
});

test("unsupported camera modules report failure without interrupting voice or claiming a flip", async () => {
  const f = fixture({ supported: false, saved: true });
  f.api.install("/voice");
  f.refresh(true);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(f.sent.at(-1)[1].supported, false);
  assert.equal(f.sent.at(-1)[1].active, false);
  assert.deepEqual(f.changes, []);
});

test("camera loader rejects changed native bytes before loading any addon", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "dpe-camera-flip-"));
  try {
    fs.writeFileSync(
      path.join(directory, "camera-flip.json"),
      JSON.stringify({ supported: true, call: 1, converter: 2, voice_sha256: "old" }),
    );
    fs.writeFileSync(path.join(directory, "discord_voice.node"), "changed");
    assert.throws(
      () => require("../../integration/camera/flip.cjs").load(directory, "/missing/helper"),
      /does not support/,
    );
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
