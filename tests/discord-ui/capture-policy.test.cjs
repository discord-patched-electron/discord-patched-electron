const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const adapter = require("../../integration/video/capture-policy.cjs");

function fixture() {
  const context = vm.createContext({});
  vm.runInContext(
    `class Store {
    static displayName = "MediaEngineStore";
    getUseSystemScreensharePicker(value) { this.lastArgument = value; return this.automatic; }
    supportsSystemScreensharePicker() { return this.supported; }
    getMediaEngine() { throw Error("Must not start or alter capture"); }
  }
  globalThis.store = new Store();
  store.automatic = false; store.supported = true;
  globalThis.ui = {
    watch(name, match, visit) { globalThis.match = match; globalThis.visit = visit; visit({A:store}); },
    inspect(runtime) { return {objects:Object.values(runtime.c.picker.exports)}; }
  };`,
    context,
  );
  const apply = (method, environment = { wayland: true, x11: false }) =>
    vm.runInContext(
      `(${adapter.toString()})(${JSON.stringify({ method, platform: "linux", ...environment })}, ui)`,
      context,
    );
  return { context, apply, store: context.store };
}

test("explicit supported portal preference changes next picker and automatic restores Discord's choice", () => {
  const f = fixture();
  f.apply("auto");
  assert.equal(f.store.getUseSystemScreensharePicker("argument"), false);
  assert.equal(f.store.lastArgument, "argument");
  const wrapped = f.store.getUseSystemScreensharePicker;
  f.apply("portal");
  assert.equal(f.store.getUseSystemScreensharePicker(), true);
  assert.equal(f.store.getUseSystemScreensharePicker, wrapped, "updates do not nest wrappers");
  f.store.supported = false;
  assert.equal(
    f.store.getUseSystemScreensharePicker(),
    false,
    "preference cannot invent native support",
  );
  f.store.supported = true;
  f.apply("auto");
  assert.equal(f.store.getUseSystemScreensharePicker(), false);
});

test("X11 override is limited to an X11 session and cannot bypass a Wayland source picker", () => {
  const f = fixture();
  f.store.automatic = true;
  f.apply("x11");
  assert.equal(f.store.getUseSystemScreensharePicker(), true);
  f.apply("x11", { wayland: false, x11: true });
  assert.equal(f.store.getUseSystemScreensharePicker(), false);
  f.apply("portal", { wayland: false, x11: true });
  f.store.automatic = false;
  assert.equal(f.store.getUseSystemScreensharePicker(), false);
});

test("KMS follows truthful system picker support on X11 and restores Automatic routing", () => {
  const f = fixture();
  f.store.supported = false;
  f.apply("kms", { wayland: false, x11: true });
  const state = vm.runInContext('globalThis[Symbol.for("dpe.capturePolicy")]', f.context);
  assert.equal(state.supported(), false);
  assert.equal(f.store.getUseSystemScreensharePicker(), false);
  f.store.supported = true;
  assert.equal(state.supported(), true);
  assert.equal(f.store.getUseSystemScreensharePicker(), true);
  assert.equal(f.store.supported, true, "the native capability is never rewritten");
  f.apply("auto", { wayland: false, x11: true });
  assert.equal(f.store.getUseSystemScreensharePicker(), false);
});

test("capture policy only discovers the recognized store and never executes capture controllers", () => {
  const f = fixture();
  f.apply("portal");
  assert.equal(f.context.match("unrelated getUseSystemScreensharePicker"), false);
  assert.equal(
    f.context.match(
      "MediaEngineStore getUseSystemScreensharePicker supportsSystemScreensharePicker",
    ),
    true,
  );
  const original = f.store.getUseSystemScreensharePicker;
  f.context.visit({ first: f.store, second: Object.create(Object.getPrototypeOf(f.store)) });
  assert.equal(
    f.store.getUseSystemScreensharePicker,
    original,
    "ambiguous exports do not get patched",
  );
});
