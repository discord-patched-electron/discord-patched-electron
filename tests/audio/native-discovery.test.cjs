const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const discover = require("../../integration/audio/native-picker-discovery.cjs");
// Independent component doubles: public prop contracts, not Discord module IDs
// or copied bundles. Calling any of these during discovery is a test failure.
const bodies = {
  ModalRoot: `function({transitionState,onClose,children,contentOutsideContainer,fullScreenOnMobile}){throw Error('must not render')}`,
  Button: `function({buttonRef,disabled,onClick,loadingStartedLabel,variant}){throw Error('must not render')}`,
  SearchBar: `function({query,onChange,onClear,placeholder}){return {role:'searchbox'}}`,
  SegmentedControl: `function({options,value,onChange,optionClassName,scrollToStart,scrollToEnd,tooltipAriaLabel}){throw Error('must not render')}`,
  Clickable: `class {renderNonInteractive(){const {ignoreKeyPress,innerRef,onClick}=this.props;throw Error('must not render')}}`,
  Scroller: `function({scrollbarGutter,disableFocusRingScope,children,dir,orientation}){throw Error('must not render')}`,
  openModal: `function({modalKey,onCloseRequest,onCloseCallback,stackingBehavior,render}){state.setState(render);throw Error('must not open')}`,
  closeModal: `function(key){const entry=state.getState().find(row=>row.key===key);state.setState(state.getState().filter(row=>row.key!==key));entry.onCloseCallback()}`,
};
function fixture(transform = (source) => source) {
  const components = Object.fromEntries(
    Object.entries(bodies).map(([name, source]) => [
      name,
      vm.runInNewContext("(" + transform(source) + ")"),
    ]),
  );
  const React = {
    version: "test",
    Component: class {},
    createElement() {},
    useState() {},
    useEffect() {},
    useLayoutEffect() {},
    useRef() {},
  };
  let calls = 0;
  const runtime = (id) => {
    calls++;
    if (!runtime.c[id]) {
      const module = { exports: {} };
      runtime.m[id](module, module.exports);
      runtime.c[id] = module;
    }
    return runtime.c[id].exports;
  };
  runtime.c = Object.fromEntries(
    [React, ...Object.values(components)].map((exports, i) => [
      "changed-" + (41 + i * 37),
      { exports: { default: exports } },
    ]),
  );
  runtime.m = {};
  return {
    components,
    React,
    runtime,
    get calls() {
      return calls;
    },
  };
}
function inspect(runtimes, extra = {}) {
  const chunks = [];
  chunks.push = (chunk) => {
    Array.prototype.push.call(chunks, chunk);
    for (const runtime of runtimes) chunk[2](runtime);
  };
  const context = { webpackChunk_renamedByDiscord: chunks, ...extra };
  const result = vm.runInNewContext(`(${discover.toString()})('fixture')`, context);
  assert.equal(chunks.length, 0, "temporary chunks are removed");
  return result;
}
test("renamed chunk globals/module IDs/default exports and a larger WASM runtime still find the UI", () => {
  const f = fixture(),
    wasm = { c: Object.fromEntries(Array.from({ length: 500 }, (_, i) => [i, { exports: {} }])) };
  const result = inspect([f.runtime, wasm]);
  assert.equal(result.reason, "ready");
  assert.equal(result.components.React, f.React);
  assert.equal(result.components.Button, f.components.Button);
  assert.equal(f.calls, 0);
});
test("quotes, comments, whitespace and local variable renaming do not determine identity", () => {
  const f = fixture((source) =>
    source
      .replaceAll("'", '"')
      .replaceAll("state", "differentStore")
      .replaceAll(",", ", /* build changed */\n")
      .replaceAll(":", " : "),
  );
  const result = inspect([f.runtime]);
  assert.equal(result.reason, "ready");
  assert.equal(result.components.SearchBar, f.components.SearchBar);
});
test("quoted and unicode-escaped property keys survive minifier changes", () => {
  const f = fixture();
  const key = Object.keys(f.runtime.c)[3];
  const search = vm.runInNewContext(
    `(function(props){const q=props["qu\\u0065ry"];return {"onChange":props.onChange,"onClear":props.onClear,"placeholder":props.placeholder,role:"searchbox",value:q}})`,
  );
  f.runtime.c[key] = { exports: { random: search } };
  assert.equal(inspect([f.runtime]).reason, "ready");
});
test("throwing getters, localization proxies, cycles and React wrappers are tolerated", () => {
  const f = fixture();
  const wrapped = {
    $$typeof: Symbol.for("react.memo"),
    type: { $$typeof: Symbol.for("react.forward_ref"), render: f.components.Button },
  };
  const buttonKey = Object.keys(f.runtime.c)[2];
  f.runtime.c[buttonKey] = { exports: { default: wrapped } };
  const poison = {};
  Object.defineProperty(poison, "broken", {
    get() {
      throw Error("uninitialized");
    },
  });
  poison.self = poison;
  f.runtime.c.poison = { exports: poison };
  f.runtime.c.intl = {
    exports: new Proxy(
      {},
      { get: (_target, key) => (key === Symbol.toStringTag ? "IntlMessagesProxy" : () => {}) },
    ),
  };
  const result = inspect([f.runtime]);
  assert.equal(result.reason, "ready");
  assert.equal(result.components.Button, wrapped);
});
test("ambiguous controls are rejected rather than choosing the shorter function", () => {
  const f = fixture();
  f.runtime.c.decoy = {
    exports: vm.runInNewContext(
      "(" + bodies.SegmentedControl.replace("must not render", "different implementation") + ")",
    ),
  };
  const result = inspect([f.runtime]);
  assert.equal(result.reason, "ambiguous-components");
  assert.ok(result.ambiguous.includes("SegmentedControl"));
});
test("virtualized scrollers are not confused with the plain children scroller", () => {
  const f = fixture();
  f.runtime.c.decoy = {
    exports: vm.runInNewContext(
      "(function({scrollbarGutter,disableFocusRingScope,children,dir,orientation,renderRow,sections,rowHeight}){})",
    ),
  };
  const result = inspect([f.runtime]);
  assert.equal(result.reason, "ready");
  assert.equal(result.components.Scroller, f.components.Scroller);
});
test("compatible imperative scrollers work when the plain variant is absent", () => {
  const f = fixture();
  const key = Object.keys(f.runtime.c)[6];
  const scroller = vm.runInNewContext(
    "(function({scrollbarGutter,disableFocusRingScope,children,dir,orientation,useImperativeHandle,getScrollerNode}){})",
  );
  f.runtime.c[key] = { exports: scroller };
  const result = inspect([f.runtime]);
  assert.equal(result.reason, "ready");
  assert.equal(result.components.Scroller, scroller);
});
test("components from separate React runtimes are never combined", () => {
  const first = fixture(),
    second = fixture(),
    complete = fixture();
  delete first.runtime.c[Object.keys(first.runtime.c)[2]];
  delete second.runtime.c[Object.keys(second.runtime.c)[0]];
  assert.notEqual(inspect([first.runtime, second.runtime]).reason, "ready");
  const result = inspect([first.runtime, second.runtime, complete.runtime]);
  assert.equal(result.reason, "ready");
  assert.equal(result.components.React, complete.React);
  assert.equal(result.components.Button, complete.components.Button);
});
test("export names disambiguate only structurally compatible components", () => {
  const f = fixture();
  f.runtime.c.named = { exports: { Button: () => null } };
  assert.equal(inspect([f.runtime]).components.Button, f.components.Button);
  const named = vm.runInNewContext(
    "(" + bodies.Button.replace("must not render", "new variant") + ")",
  );
  f.runtime.c.named = { exports: { Button: named } };
  assert.equal(inspect([f.runtime]).components.Button, named);
});
test("ambiguous modal APIs are reported as ambiguous, not missing startup chunks", () => {
  const f = fixture();
  f.runtime.c.decoy = {
    exports: vm.runInNewContext(
      "(" + bodies.openModal.replace("must not open", "different modal API") + ")",
    ),
  };
  const result = inspect([f.runtime]);
  assert.equal(result.reason, "ambiguous-components");
  assert.ok(result.ambiguous.includes("openModal"));
});
test("a unique registered pill presentation module can initialize; a second candidate cannot", () => {
  const f = fixture(),
    key = Object.keys(f.runtime.c)[4];
  delete f.runtime.c[key];
  const factory = vm.runInNewContext(
    "(function(module,exports){exports.changed=" + bodies.SegmentedControl + "})",
  );
  f.runtime.m.newChunk = factory;
  assert.equal(inspect([f.runtime]).reason, "ready");
  assert.equal(f.calls, 1);
  delete f.runtime.c.newChunk;
  f.runtime.m.other = factory;
  assert.equal(inspect([f.runtime]).reason, "missing-components");
  assert.equal(f.calls, 1);
});
test("modules with capture hooks are never initialized by a signature match", () => {
  const f = fixture();
  delete f.runtime.c[Object.keys(f.runtime.c)[4]];
  f.runtime.m.controller = vm.runInNewContext(
    "(function(module,exports){getDisplayMedia();exports.changed=" + bodies.SegmentedControl + "})",
  );
  assert.equal(inspect([f.runtime]).reason, "missing-components");
  assert.equal(f.calls, 0);
});
test("absent/changed icons do not disable native controls and named SVG icons can change paths", () => {
  const f = fixture();
  let result = inspect([f.runtime]);
  assert.equal(result.reason, "ready");
  assert.equal(result.found.AppIcon, false);
  const BrowserIcon = vm.runInNewContext(
    '(function(){return {tag:"svg",path:"a completely new icon"}})',
  );
  f.runtime.c.icon = { exports: { BrowserIcon } };
  result = inspect([f.runtime]);
  assert.equal(result.components.AppIcon, BrowserIcon);
});
test("a replacement export is rediscovered; failed lookups are not cached across opens", () => {
  const f = fixture(),
    key = Object.keys(f.runtime.c)[2],
    original = f.runtime.c[key];
  delete f.runtime.c[key];
  assert.equal(inspect([f.runtime]).reason, "missing-components");
  f.runtime.c[key] = original;
  assert.equal(inspect([f.runtime]).reason, "ready");
  const replacement = vm.runInNewContext("(" + bodies.Button + ")");
  f.runtime.c[key] = { exports: replacement };
  assert.equal(inspect([f.runtime]).components.Button, replacement);
});
