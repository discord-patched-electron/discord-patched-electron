const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const script = require("../../integration/desktop/kwin-popout.cjs");
const { identify } = require("../../integration/desktop/popout-position.cjs");

function fixture() {
  const window = {
    pid: process.pid,
    internalId: "aabbccdd-1234-4321-abcd-123456789012",
    caption: "PiP",
    active: true,
    frameGeometry: { x: 400, y: 300, width: 508, height: 330 },
    clientGeometry: { x: 404, y: 326, width: 500, height: 300 },
    output: { geometry: { x: 0, y: 0, width: 1600, height: 1000 }, scale: 1.25 },
    desktops: [{ id: "workspace-1" }],
  };
  const area = { x: 20, y: 30, width: 1540, height: 920 };
  const workspace = {
    activeWindow: window,
    windowList: () => [window],
    clientArea: (kind, w) => {
      assert.equal(kind, 2);
      assert.equal(w, window);
      return area;
    },
  };
  const run = (request) =>
    JSON.parse(
      JSON.stringify(
        vm.runInNewContext(`(${script})(${JSON.stringify(request)})`, {
          workspace,
          KWin: { MaximizeArea: 2 },
        }),
      ),
    );
  return { window, workspace, area, run };
}

test("KWin uses the owned active window, decorated frame and panel-reserved area", () => {
  const f = fixture();
  const result = f.run({ pid: process.pid });
  assert.deepEqual(result.bounds, f.window.frameGeometry);
  assert.deepEqual(result.area, f.area);
  assert.equal(result.client.width, 500);
  assert.equal(result.scale, 1.25);
  assert.equal(result.workspace, "workspace-1");
  const moved = f.run({
    pid: process.pid,
    id: result.id,
    before: result.bounds,
    target: { x: 1036, y: 604 },
  });
  assert.deepEqual(moved.bounds, { x: 1036, y: 604, width: 508, height: 330 });
});

test("KWin rejects foreign, tiled, special, hidden and fullscreen windows", () => {
  for (const [key, value] of [
    ["pid", process.pid + 1],
    ["tile", {}],
    ["specialWindow", true],
    ["minimized", true],
    ["fullScreen", true],
    ["maximizeMode", 1],
  ]) {
    const f = fixture();
    f.window[key] = value;
    assert.equal(f.run({ pid: process.pid }), null, key);
  }
});

test("KWin rejects stale identities, geometry changes and out-of-area targets", () => {
  for (const change of ["id", "before", "nan", "left", "right", "bottom"]) {
    const f = fixture();
    const before = { ...f.window.frameGeometry };
    const request = { pid: process.pid, id: f.window.internalId, before, target: { x: 20, y: 30 } };
    if (change === "id") request.id = "different";
    if (change === "before") before.x += 50;
    if (change === "nan") request.target.x = null;
    if (change === "left") request.target.x = 0;
    if (change === "right") request.target.x = 1500;
    if (change === "bottom") request.target.y = 900;
    assert.equal(f.run(request), null, change);
    assert.equal(f.window.frameGeometry.x, 400);
  }
});

test("KWin placement follows its center-preserving resize, without following workspace changes", async () => {
  for (const change of ["resize", "top-resize", "move", "workspace", "focus"]) {
    const f = fixture();
    const run = async (command, args) => {
      assert.equal(command, "/usr/bin/python3");
      assert.equal(args[0], "-I");
      // Execute the exact serialized request supplied to the Python bridge.
      const data = vm.runInNewContext(`(${args.at(-1)})()`, {
        workspace: f.workspace,
        KWin: { MaximizeArea: 2 },
      });
      return { stdout: JSON.stringify(data) };
    };
    const controller = identify(
      { isDestroyed: () => false, isFocused: () => change !== "focus", getTitle: () => "PiP" },
      run,
      { WAYLAND_DISPLAY: "fixture", XDG_CURRENT_DESKTOP: "KDE" },
      change === "top-resize" ? "top-right" : "bottom-right",
    );
    await controller.place(f.area);
    const before = { ...f.window.frameGeometry };
    if (change === "focus") {
      assert.equal(before.x, 400);
      continue;
    }
    f.window.frameGeometry = {
      x: before.x + 50,
      y: Math.max(f.area.y, Math.min(f.area.y + f.area.height - 430, before.y - 50)),
      width: 408,
      height: 430,
    };
    f.window.clientGeometry = { width: 400, height: 400 };
    if (change === "top-resize") f.window.frameGeometry.y = f.area.y;
    if (change === "move") f.window.frameGeometry.x += 100;
    if (change === "workspace") f.window.desktops = [{ id: "workspace-2" }];
    const preserved = f.window.frameGeometry.y;
    await controller.place(f.area, { width: 400, height: 400 });
    assert.equal(
      f.window.frameGeometry.y,
      change === "resize" ? 504 : change === "top-resize" ? 46 : preserved,
      change,
    );
  }
});

test("missing KDE bridge dependencies leave compositor placement alone", async () => {
  const controller = identify(
    { isDestroyed: () => false, isFocused: () => true },
    async () => {
      throw new Error("No dbus-python");
    },
    { WAYLAND_DISPLAY: "fixture", XDG_CURRENT_DESKTOP: "KDE" },
  );
  assert.equal(await controller.display({}), undefined);
  await controller.place({ x: 0, y: 0, width: 1000, height: 800 });
});
