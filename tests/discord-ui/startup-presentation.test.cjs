const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const startup = require("../../integration/discord-ui/startup-presentation.cjs");

test("presentation warms at startup, retries late assets and cleans up without settings", () => {
  const timers = new Map(),
    events = new Map();
  let next = 0,
    captures = 0;
  const context = vm.createContext({
    document: {},
    setTimeout(fn, delay) {
      assert.equal(delay, 5100);
      timers.set(++next, fn);
      return next;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    addEventListener(name, fn) {
      events.set(name, fn);
    },
    collect(request) {
      captures++;
      assert.equal(request.iconsOnly, true);
      if (captures === 1) throw Error("Styles not ready");
    },
  });
  const code = `(${startup})({}, {}, collect)`;
  vm.runInContext(code, context);
  assert.equal(captures, 1);
  vm.runInContext(code, context);
  assert.equal(captures, 1, "reinjecting does not create a second timer");
  while (timers.size) {
    const [id, fn] = timers.entries().next().value;
    timers.delete(id);
    fn();
  }
  assert.equal(captures, 7, "startup scanning is bounded");
  events.get("pagehide")();
  assert.equal(timers.size, 0);
});

test("navigation cancels pending asset warmup", () => {
  let tick,
    stop,
    cleared = false;
  const context = vm.createContext({
    document: {},
    setTimeout(fn) {
      tick = fn;
      return 1;
    },
    clearTimeout(id) {
      cleared = id === 1;
    },
    addEventListener(_name, fn) {
      stop = fn;
    },
    collect() {},
  });
  vm.runInContext(`(${startup})({}, {}, collect)`, context);
  stop();
  tick();
  assert.equal(cleared, true);
});

test("startup fills the same native icon cache the picker reads, before settings exists", () => {
  const presentation = require("../../integration/discord-ui/presentation.cjs");
  const root = { classList: { contains: () => false } };
  const context = vm.createContext({
    document: {
      documentElement: root,
      styleSheets: [],
      querySelector: () => null,
      querySelectorAll: () => [],
    },
    matchMedia: () => ({ matches: false }),
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
    ui: {
      runtimes: () => [{}],
      safe: (value, name) => value?.[name],
      inspect: () => ({
        objects: [],
        choose: (name) =>
          name === "ScreenIcon"
            ? () => ({
                type: "svg",
                props: { viewBox: "0 0 24 24", children: { type: "path", props: { d: "M5 2" } } },
              })
            : undefined,
      }),
      unwrap: (value) => value,
    },
  });
  vm.runInContext(`(${startup})({}, ui, ${presentation})`, context);
  const icon = vm.runInContext(
    'globalThis[Symbol.for("dpe.discordPresentation")].icons.screen',
    context,
  );
  assert.match(icon, /^<svg/);
  assert.match(icon, /M5 2/);
});
