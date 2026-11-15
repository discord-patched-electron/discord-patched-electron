const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const runtime = require("../../integration/discord-ui/runtime.cjs");
function fixture() {
  const jobs = [],
    observers = [],
    listeners = new Set(),
    errors = [];
  const context = vm.createContext({
    console: { warn: (...args) => errors.push(args) },
    queueMicrotask: (job) => jobs.push(job),
    addEventListener: (_event, callback) => listeners.add(callback),
    removeEventListener: (_event, callback) => listeners.delete(callback),
    MutationObserver: class {
      constructor(callback) {
        this.callback = callback;
        observers.push(this);
      }
      observe() {}
      disconnect() {
        this.disconnected = true;
      }
    },
  });
  return {
    controls: vm.runInContext(`(${runtime})()`, context).controls,
    flush: () => {
      while (jobs.length) jobs.shift()();
    },
    observers,
    listeners,
    errors,
  };
}
const plain = (value) => JSON.parse(JSON.stringify(value));
test("named controls update callbacks without duplicating frozen native lists or losing other mods", () => {
  const { controls } = fixture();
  const native = Object.freeze([{ key: "mod" }, { key: "anchor" }]);
  let clicked;
  const old = controls.register("test", () => ({
    key: "test",
    click: () => {
      clicked = "old";
    },
  }));
  const options = { id: "test", before: (node) => node.key === "anchor" };
  const first = controls.insert(native, options);
  const remove = controls.register("test", () => ({
    key: "test",
    click: () => {
      clicked = "new";
    },
  }));
  old(); // A stale owner cannot unregister its replacement.
  const next = controls.insert(first, options);
  assert.deepEqual(plain(next.map((node) => node.key)), ["mod", "test", "anchor"]);
  next[1].click();
  assert.equal(clicked, "new");
  assert.equal(native.length, 2);
  assert.equal(next[0], native[0]);
  remove();
  assert.deepEqual(plain(controls.insert(next, options)), plain(native));
  assert.equal(controls.insert(native, { ...options, before: () => false }), native);
});
test("provider traversal preserves native identity, refs and siblings and contains broken extensions", () => {
  const { controls, errors } = fixture();
  const React = {
    cloneElement: (node, props) => ({ ...node, props: { ...node.props, ...props } }),
  };
  const control = Object.freeze({ type: "native", props: Object.freeze({ label: "Return" }) });
  const sibling = Object.freeze({ type: "other", props: {} });
  const root = Object.freeze({
    type: "provider",
    ref: {},
    key: "root",
    props: Object.freeze({ children: Object.freeze([control, sibling]) }),
  });
  assert.equal(
    controls.tree(React, root, (node) => node),
    root,
  );
  const next = controls.tree(React, root, (node) =>
    node === control ? React.cloneElement(node, { visible: true }) : node,
  );
  assert.equal(next.ref, root.ref);
  assert.equal(next.key, root.key);
  assert.equal(next.props.children[1], sibling);
  assert.equal(next.props.children[0].props.visible, true);
  assert.equal(control.props.visible, undefined);
  assert.equal(
    controls.tree(React, root, () => {
      throw Error("changed upstream");
    }),
    root,
  );
  controls.register("broken", () => {
    throw Error("broken control");
  });
  assert.equal(controls.render("broken"), null);
  assert.equal(errors.length, 2);
});
test("independent wrappers coexist on frozen exports and retain live unrelated getters", () => {
  const { controls } = fixture();
  let marker = 1;
  const source = Object.freeze({
    first: () => 1,
    second: () => 2,
    get marker() {
      return marker;
    },
  });
  const entry = { exports: source },
    req = { c: { module: entry } };
  const first = () => 10,
    second = () => 20;
  assert.equal(controls.replaceExport(req, source, "first", first), true);
  assert.equal(controls.replaceExport(req, source, "second", second), true);
  assert.equal(entry.exports.first(), 10);
  assert.equal(entry.exports.second(), 20);
  assert.equal(source.first(), 1);
  marker = 3;
  assert.equal(entry.exports.marker, 3);
  assert.deepEqual(Object.keys(entry.exports), Object.keys(source));
  assert.equal(Object.getOwnPropertyDescriptor(entry.exports, "first").value, first);
  assert.equal(
    controls.replaceExport(
      { c: { a: { exports: source }, b: { exports: source } } },
      source,
      "first",
      first,
    ),
    false,
  );
});
test("DOM mounts coalesce repairs, survive native remounts and release observers and pending work", () => {
  const { controls, observers, listeners, flush } = fixture();
  let repairs = 0,
    cleaned = 0;
  const config = { root: {}, repair: () => repairs++, cleanup: () => cleaned++ };
  const mount = controls.observe("footer", config);
  assert.equal(controls.observe("footer", config), mount);
  observers[0].callback([]);
  observers[0].callback([]);
  flush();
  assert.equal(repairs, 1);
  observers[0].callback([]);
  flush();
  assert.equal(repairs, 2);
  mount.refresh();
  for (const listener of listeners) listener();
  flush();
  mount.dispose();
  assert.equal(repairs, 2);
  assert.equal(cleaned, 1);
  assert.equal(observers[0].disconnected, true);
  assert.equal(listeners.size, 0);
  assert.notEqual(controls.observe("footer", config), mount);
  flush();
  assert.equal(repairs, 3);
});
