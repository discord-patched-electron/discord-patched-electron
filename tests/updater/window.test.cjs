const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

function fixture(writeText) {
  const handlers = new Map();
  const contents = Object.assign(new EventEmitter(), {
    mainFrame: {},
    ipc: { handle: (name, handler) => handlers.set(name, handler) },
    setWindowOpenHandler() {},
    send() {},
  });
  const window = Object.assign(new EventEmitter(), {
    webContents: contents,
    isDestroyed: () => false,
    loadURL: () => Promise.resolve(),
    show() {},
  });
  const module = { exports: {} };
  vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "../../integration/updater/window.cjs"), "utf8"),
    {
      module,
      __dirname,
      process: { env: { DPE_PROFILE_DIR: "/profile", DPE_APP_ROOT: "/app" }, argv: [] },
      console,
      require(name) {
        if (name === "electron")
          return {
            BrowserWindow: function () {
              return window;
            },
            clipboard: { writeText },
            ipcMain: new EventEmitter(),
            net: {},
            protocol: { registerSchemesAsPrivileged() {}, handle() {} },
          };
        if (name === "./profile.cjs") return { background: () => "#282b30" };
        if (name.startsWith("./")) return require(`../../integration/updater/${name.slice(2)}`);
        return require(name);
      },
    },
  );
  const view = module.exports(() => {});
  return {
    view,
    copy: (frame = contents.mainFrame, ...args) =>
      handlers.get("dpe-splash-copy-details")({ senderFrame: frame }, ...args),
  };
}

test("copy details accepts only the updater main frame and the current error", async () => {
  const copied = [];
  const f = fixture((text) => copied.push(text));
  assert.equal(await f.copy(), false);
  f.view.update({ stage: "error", details: "diagnostic one" });
  assert.equal(await f.copy({}), false);
  assert.equal(await f.copy(undefined, "renderer-supplied text"), true);
  assert.deepEqual(copied, ["diagnostic one"]);
  f.view.update({ stage: "error", details: "diagnostic two" });
  assert.equal(await f.copy(), true);
  assert.deepEqual(copied, ["diagnostic one", "diagnostic two"]);
  f.view.update({ stage: "ready" });
  assert.equal(await f.copy(), false);
  assert.equal(copied.length, 2);
});

test("copy details waits for clipboard completion and reports clipboard failures", async () => {
  let finish;
  const f = fixture(() => new Promise((resolve) => (finish = resolve)));
  f.view.update({ stage: "error", details: "diagnostic" });
  let completed = false;
  const pending = f.copy().then((success) => {
    completed = true;
    return success;
  });
  await Promise.resolve();
  assert.equal(completed, false);
  finish();
  assert.equal(await pending, true);
  for (const writeText of [
    () => {
      throw new Error("Clipboard unavailable");
    },
    () => Promise.reject(new Error("Clipboard unavailable")),
  ]) {
    const failed = fixture(writeText);
    failed.view.update({ stage: "error", details: "diagnostic" });
    assert.equal(await failed.copy(), false);
  }
});
