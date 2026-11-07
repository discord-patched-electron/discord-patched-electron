const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const source = require("../../integration/discord-ui/source.cjs");
const adapter = require("../../integration/discord-ui/capture-picker.cjs");
const makeView = require("../../integration/discord-ui/capture-picker-view.cjs");

const plain = (value) => JSON.parse(JSON.stringify(value));
function nodes(tree, predicate) {
  const found = [];
  function visit(node) {
    if (Array.isArray(node)) return node.forEach(visit);
    if (!node || typeof node !== "object" || !node.props) return;
    if (predicate(node)) found.push(node);
    visit(node.props.children);
  }
  visit(tree);
  return found;
}

function fixture({
  cached = false,
  ambiguous = false,
  contract = "native",
  enabled = true,
  lastTab,
} = {}) {
  const calls = [],
    executed = [],
    effects = [],
    hooks = [];
  const events = new Map(),
    timers = new Map();
  let cursor = 0,
    timer = 0,
    sequence = 0;
  let holdArm, armReply;
  const element = (type, props, ...children) => ({
    type,
    props: {
      ...props,
      ...(children.length ? { children: children.length === 1 ? children[0] : children } : {}),
    },
  });
  const same = (left, right) =>
    left && right && left.length === right.length && left.every((value, i) => value === right[i]);
  const React = {
    version: "fixture",
    Fragment: Symbol.for("react.fragment"),
    Component: class Component {
      constructor(props) {
        this.props = props;
      }
    },
    createElement: element,
    cloneElement(node, props, ...children) {
      return element(node.type, { ...node.props, ...props }, ...(children.length ? children : []));
    },
    useState(initial) {
      const index = cursor++;
      hooks[index] ||= { value: typeof initial === "function" ? initial() : initial };
      return [
        hooks[index].value,
        (value) => {
          hooks[index].value = typeof value === "function" ? value(hooks[index].value) : value;
        },
      ];
    },
    useRef(initial) {
      const index = cursor++;
      hooks[index] ||= { current: initial };
      return hooks[index];
    },
    useCallback(callback, dependencies) {
      const index = cursor++;
      if (!same(hooks[index]?.dependencies, dependencies))
        hooks[index] = { value: callback, dependencies };
      return hooks[index].value;
    },
    useEffect(callback, dependencies) {
      const index = cursor++;
      if (same(hooks[index]?.dependencies, dependencies)) return;
      effects.push(() => {
        hooks[index]?.cleanup?.();
        hooks[index] = { dependencies, cleanup: callback() };
      });
    },
    useLayoutEffect() {},
  };
  const window = {
    localStorage: {
      getItem: () => lastTab,
      setItem: (_key, value) => {
        lastTab = value;
      },
    },
    addEventListener(type, listener) {
      if (!events.has(type)) events.set(type, new Set());
      events.get(type).add(listener);
    },
    removeEventListener(type, listener) {
      events.get(type)?.delete(listener);
    },
    dispatchEvent(event) {
      for (const listener of events.get(event.type) || []) listener(event);
    },
  };
  const CustomEvent = class CustomEvent {
    constructor(type, options) {
      this.type = type;
      this.detail = options.detail;
    }
  };
  const reply = (request, value, error) =>
    window.dispatchEvent(
      new CustomEvent("dpe-capture-picker-reply", {
        detail: JSON.stringify({ id: request.id, value, error }),
      }),
    );
  const catalog = [
    { token: "screen-token", kind: "screen", name: "Screen 1 (DP-2)", available: true },
    { token: "game-token", kind: "window", name: "Owned game", available: true },
  ];
  window.addEventListener("dpe-capture-picker-request", (event) => {
    const request = JSON.parse(event.detail);
    calls.push({
      op: request.op,
      token: request.token,
      ...(typeof request.handoff === "boolean" ? { handoff: request.handoff } : {}),
    });
    if (request.op === "arm" && holdArm) {
      armReply = () => reply(request, { armed: true });
      return;
    }
    reply(request, request.op === "list" ? { enabled: true, sources: catalog } : { armed: true });
  });
  const styles = new Map();
  const context = vm.createContext({
    React,
    calls,
    window,
    CustomEvent,
    console,
    crypto: { randomUUID: () => `00000000-0000-0000-0000-${String(++sequence).padStart(12, "0")}` },
    setTimeout: (callback) => {
      timers.set(++timer, callback);
      return timer;
    },
    clearTimeout: (id) => timers.delete(id),
    setInterval: (callback) => {
      timers.set(++timer, callback);
      return timer;
    },
    clearInterval: (id) => timers.delete(id),
    document: {
      getElementById: (id) => styles.get(id),
      createElement: () => ({}),
      head: { append: (node) => styles.set(node.id, node) },
    },
    fixtureContract: contract,
  });
  vm.runInContext(
    `
    function frozen(type, props) { return Object.freeze({type, props:Object.freeze(props)}); }
    globalThis.nativeState = Object.freeze({sourceType:"window",nativeSourceType:"screen",preset:7,resolution:1080,fps:60,muteStreamAudio:false});
    globalThis.nativeSelection = () => calls.push({op:"native-selection"});
    globalThis.deviceIcon = function DeviceIcon() {};
    globalThis.windowIcon = function WindowIcon() {};
    globalThis.NativeMenu = function NativeMenu() {
      return frozen("menu", {children: [frozen("MenuItem", {id:"mute",label:"Mute",action(){}}), frozen("MenuItem", {id:"advanced-items",label:"Advanced",children:frozen("MenuItem",{id:"native-preview"})})]});
    };
    globalThis.SettingsCog = function SettingsCog() {
      return frozen("Popout", { targetElementRef: null, renderPopout: ({closePopout}) => frozen(NativeMenu,{onClose:closePopout}), "aria-label": "Settings" });
    };
    globalThis.nativeChange = ({value}) => nativeDispatch({type:"set_source_type",sourceType:value});
    globalThis.nativeDispatch = (action) => {
      calls.push({op:"dispatch",...action});
      if(action.type === "set_source_type") nativeState = Object.freeze({...nativeState,sourceType:action.sourceType});
      if(action.type === "set_native_source_type") nativeState = Object.freeze({...nativeState,nativeSourceType:action.sourceType});
      rebuild();
    };
    globalThis.rebuild = () => {
      const state = nativeState;
      globalThis.nativeStart = function () {
        const {nativeSourceType, selectedChannel, selectedSource} = state;
        calls.push({op:"native-start",nativeSourceType,selectedChannel,selectedSource,resolution:state.resolution,fps:state.fps,muteStreamAudio:state.muteStreamAudio});
        globalThis.onNativeStart?.();
      };
      globalThis.options = Object.freeze([
        Object.freeze({name:"Applications",value:"window",icon:windowIcon}),
        Object.freeze({name:"Devices",value:"camera",icon:deviceIcon})
      ]);
      globalThis.tabs = frozen("NativeTabs",{role:"tablist",look:"pill",options,value:state.sourceType,onChange:nativeChange});
      globalThis.pane = frozen("NativeSourcePane",{onSourceSelect:nativeSelection});
      globalThis.devicePane = frozen("NativeDevicePane",{onClick:()=>{}});
      globalThis.start = frozen("NativeButton",{variant:"primary",text:"Choose",onClick:nativeStart,disabled:false});
      globalThis.quality = frozen("NativeQuality",{preset:state.preset,resolution:state.resolution,fps:state.fps,sound:!state.muteStreamAudio});
      globalThis.cog = frozen(SettingsCog, {align:"right"});
      let sources = state.sourceType === "camera" ? devicePane : pane;
      if (fixtureContract === "missing-pane") sources = frozen("ChangedPane",{});
      const tabNodes = fixtureContract === "ambiguous-tabs" ? [tabs,tabs] : tabs;
      const paneNodes = fixtureContract === "ambiguous-panes" ? [sources,sources] : sources;
      globalThis.fixtureTree = frozen("NativeProvider",{state,dispatch:nativeDispatch,children:frozen("NativeModal",{size:"xl",children:Object.freeze([tabNodes,paneNodes,quality,start,cog])})});
    };
    rebuild();
  `,
    context,
  );
  const factory = vm.runInContext(
    `(function(module) {
    function Original(props) {
      const useCreateGoLiveModalState = "GO_LIVE_MODAL_V2";
      const actions = ["set_source_type", "onSourceSelect"];
      void useCreateGoLiveModalState; void actions; void props;
      return fixtureTree;
    }
    Object.defineProperty(module.exports,"default",{enumerable:true,configurable:false,get:()=>Original});
    module.exports.marker = Object.freeze({native:true});
    ${
      ambiguous
        ? `module.exports.another = function Other() {
      const useCreateGoLiveModalState = "GO_LIVE_MODAL_V2";
      const actions = ["set_source_type", "onSourceSelect"];
      void useCreateGoLiveModalState; void actions; return fixtureTree;
    };`
        : ""
    }
  })`,
    context,
  );
  const runtime = (id) => {
    if (!runtime.c[id]) {
      const module = { exports: {} };
      runtime.c[id] = module;
      executed.push(id);
      runtime.m[id](module, module.exports, runtime);
    }
    return runtime.c[id].exports;
  };
  runtime.m = cached ? { picker: factory } : {};
  runtime.c = { react: { exports: React } };
  const chunks = [];
  chunks.push = function (chunk) {
    Array.prototype.push.call(this, chunk);
    Object.assign(runtime.m, chunk[1]);
    return chunk[2]?.(runtime);
  };
  context.webpackChunkdiscord_app = chunks;
  if (cached) runtime("picker");
  const install = (value = enabled) =>
    vm.runInContext(
      source.call(
        adapter,
        {
          platform: "linux",
          experimentalCapture: value,
          styles: ".dpe-capture-list{}",
          icons: { bolt: '<svg viewBox="0 0 24 24"></svg>' },
        },
        makeView,
      ),
      context,
    );
  const load = () => chunks.push([["capture"], { picker: factory }, (req) => req("picker")]);
  function render() {
    const result = runtime("picker").default({});
    if (typeof result.type !== "function" || typeof result.type.prototype?.render !== "function")
      return result;
    const enhance = new result.type(result.props).render();
    cursor = 0;
    return enhance.type(enhance.props);
  }
  async function flush() {
    while (effects.length) effects.shift()();
    await Promise.resolve();
    await Promise.resolve();
  }
  return {
    context,
    runtime,
    calls,
    executed,
    catalog,
    install,
    load,
    render,
    flush,
    holdArm(value = true) {
      holdArm = value;
    },
    finishArm() {
      armReply?.();
    },
    remount() {
      for (const hook of hooks) hook?.cleanup?.();
      hooks.length = 0;
      return render();
    },
    async ready() {
      install();
      if (!cached) load();
      render();
      await flush();
      return render();
    },
  };
}

for (const cached of [false, true]) {
  test(`optimized tab extends ${cached ? "cached" : "lazy"} native modal without mutating native elements`, () => {
    const f = fixture({ cached });
    f.install();
    assert.deepEqual(f.executed, cached ? ["picker"] : []);
    if (!cached) f.load();
    const originalTree = f.context.fixtureTree;
    const tree = f.render();
    const tabs = nodes(tree, (node) => node.props.role === "tablist")[0];
    assert.deepEqual(
      Array.from(tabs.props.options, (option) => [option.value, option.name]),
      [
        ["dpe-optimized", "Optimized"],
        ["window", "Vanilla"],
        ["camera", "Devices"],
      ],
    );
    assert.equal(tabs.props.value, "dpe-optimized");
    assert.equal(tabs.props.options[2], f.context.options[1]);
    assert.equal(
      nodes(tree, (node) => node.props.onSourceSelect === f.context.nativeSelection)[0],
      f.context.pane,
    );
    const hidden = nodes(tree, (node) => node.props.hidden === true)[0];
    assert.equal(hidden.props.children, f.context.pane);
    assert.equal(hidden.props.inert, true);
    assert.equal(nodes(tree, (node) => node.type === "NativeQuality")[0], f.context.quality);
    assert.equal(tree.props.state, originalTree.props.state);
    assert.equal(f.context.tabs.props.value, "window");
    assert.equal(f.context.options.length, 2);
    assert.equal(f.context.options[0].name, "Applications");
    assert.equal(originalTree, f.context.fixtureTree);
    const exports = f.runtime("picker");
    assert.equal(Object.getOwnPropertyDescriptor(exports, "default").configurable, false);
    assert.equal(exports.marker.native, true);
    const wrapped = exports.default;
    f.install();
    assert.equal(f.runtime("picker").default, wrapped);
  });
}

test("Vanilla and Devices retain original callbacks and device pane", async () => {
  const f = fixture();
  let tree = await f.ready();
  nodes(tree, (node) => node.props.role === "tablist")[0].props.onChange({ value: "window" });
  tree = f.render();
  assert.equal(nodes(tree, (node) => node.props.role === "tablist")[0].props.value, "window");
  assert.equal(
    nodes(tree, (node) => node.type === "NativeButton")[0].props.onClick,
    f.context.nativeStart,
  );
  assert.equal(
    nodes(tree, (node) => node.props.hidden === false)[0].props.children,
    f.context.pane,
  );
  nodes(tree, (node) => node.props.role === "tablist")[0].props.onChange({ value: "camera" });
  tree = f.render();
  assert.equal(nodes(tree, (node) => node.props.role === "tablist")[0].props.value, "camera");
  assert.equal(nodes(tree, (node) => node.type === "NativeDevicePane")[0], f.context.devicePane);
  assert.equal(nodes(tree, (node) => node.props.sources !== undefined).length, 0);
});

test("selected optimized source arms once then uses Discord's original start with unchanged quality/audio", async () => {
  const f = fixture();
  let tree = await f.ready();
  const panel = nodes(tree, (node) => Array.isArray(node.props.sources))[0];
  assert.equal(panel.props.sources.length, 3);
  panel.props.onSelect("game-token");
  tree = f.render();
  const footer = nodes(tree, (node) => node.type === "NativeButton")[0];
  assert.equal(footer.props.disabled, false);
  await footer.props.onClick();
  assert.deepEqual(plain(f.calls.filter((call) => call.op === "arm")), [
    { op: "arm", token: "game-token" },
  ]);
  assert.deepEqual(plain(f.calls.filter((call) => call.op === "native-start")), [
    {
      op: "native-start",
      nativeSourceType: "window",
      resolution: 1080,
      fps: 60,
      muteStreamAudio: false,
    },
  ]);
});

test("experimental off and changed or ambiguous native layouts stay untouched", () => {
  for (const options of [
    { enabled: false },
    { contract: "missing-pane" },
    { contract: "ambiguous-tabs" },
    { contract: "ambiguous-panes" },
    { ambiguous: true },
  ]) {
    const f = fixture(options);
    f.install();
    f.load();
    assert.equal(f.render(), f.context.fixtureTree);
    assert.equal(f.context.options[0].name, "Applications");
    assert.equal(
      f.calls.some((call) => call.op === "arm" || call.op === "native-start"),
      false,
    );
  }
});

test("turning experimental off after mounting restores the original picker tree", async () => {
  const f = fixture();
  await f.ready();
  f.install(false);
  assert.equal(f.render(), f.context.fixtureTree);
  f.install(true);
  const tabs = nodes(f.render(), (node) => node.props.role === "tablist")[0];
  assert.equal(tabs.props.value, "dpe-optimized");
});

test("source view orders screens before games and uses distinct shared source icons", () => {
  const h = (type, props, ...children) => ({ type, props: { ...props, children } });
  const View = makeView(
    {
      createElement: h,
      useRef: (value) => ({ current: value }),
      useState: (value) => [value, () => {}],
      useEffect: () => {},
    },
    (name) => `<svg data-icon="${name}"></svg>`,
  );
  const selected = [];
  const tree = View({
    sources: [
      { token: "game", kind: "window", name: "Game", available: true },
      { token: "screen", kind: "screen", name: "Screen 1 (DP-2)", available: true },
      {
        token: "blocked",
        kind: "screen",
        name: "Screen 2 (DP-3)",
        available: false,
        reason: "Permission required",
      },
    ],
    selected: "game",
    onSelect: (token) => selected.push(token),
  });
  const options = nodes(tree, (node) => node.props.role === "option");
  assert.deepEqual(
    options.map((node) => node.props["data-dpe-capture-source"]),
    ["screen", "blocked", "game"],
  );
  assert.equal(options[1].props.disabled, true);
  assert.equal(options[2].props.tabIndex, 0);
  assert.equal(options[2].props["aria-selected"], true);
  assert.deepEqual(
    nodes(tree, (node) => node.props.className === "dpe-capture-source-icon").map(
      (node) => node.props.name,
    ),
    ["screen", "screen", "applications"],
  );
  options[0].props.onClick();
  options[1].props.onClick();
  assert.deepEqual(selected, ["screen"]);
});

test("disabling during arm prevents a late native start and re-enabling clears the busy state", async () => {
  const f = fixture();
  let tree = await f.ready();
  nodes(tree, (node) => Array.isArray(node.props.sources))[0].props.onSelect("screen-token");
  tree = f.render();
  f.holdArm();
  const starting = nodes(tree, (node) => node.type === "NativeButton")[0].props.onClick();
  await f.flush();
  f.install(false);
  assert.equal(f.render(), f.context.fixtureTree);
  f.install(true);
  f.finishArm();
  await starting;
  assert.equal(
    f.calls.some((call) => call.op === "native-start"),
    false,
  );
  const footer = nodes(f.render(), (node) => node.type === "NativeButton")[0];
  assert.equal(footer.props.disabled, false);
});

test("Other window opens the native picker unarmed and refresh sits beside the native cog", async () => {
  const f = fixture();
  let tree = await f.ready();
  const panel = nodes(tree, (node) => Array.isArray(node.props.sources))[0];
  assert.equal(panel.props.sources.at(-1).name, "Other window (Vanilla)");
  assert.equal(panel.props.onRefresh, undefined);
  const refresh = nodes(tree, (node) => node.props["aria-label"] === "Refresh sources");
  assert.equal(refresh.length, 1);
  const cog = nodes(tree, (node) => node.props.align === "right")[0];
  assert.ok(cog);
  assert.notEqual(cog.type, f.context.SettingsCog, "native cog receives a semantic popout wrapper");
  panel.props.onSelect("dpe-vanilla-window");
  tree = f.render();
  await nodes(tree, (node) => node.type === "NativeButton")[0].props.onClick();
  assert.equal(
    f.calls.some((call) => call.op === "arm"),
    false,
  );
  assert.equal(
    f.calls.filter((call) => call.op === "native-start").at(-1).nativeSourceType,
    "window",
  );
});

test("last selected tab survives reopening and restores Devices through the native tab callback", async () => {
  for (const tab of ["window", "camera"]) {
    const f = fixture({ lastTab: tab });
    let tree = await f.ready();
    assert.equal(nodes(tree, (node) => node.props.role === "tablist")[0].props.value, tab);
    nodes(tree, (node) => node.props.role === "tablist")[0].props.onChange({
      value: "dpe-optimized",
    });
    f.render();
    f.remount();
    await f.flush();
    tree = f.render();
    assert.equal(
      nodes(tree, (node) => node.props.role === "tablist")[0].props.value,
      "dpe-optimized",
    );
  }
});

test("cog preserves native entries and inserts a restricted settings action above Advanced", async () => {
  const f = fixture();
  const tree = await f.ready();
  const cog = nodes(tree, (node) => node.props.align === "right")[0];
  const popout = cog.type(cog.props);
  let closed = 0;
  const menu = popout.props.renderPopout({ closePopout: () => closed++ });
  const menuTree = menu.type(menu.props);
  assert.deepEqual(
    nodes(menuTree, (node) => node.props.id).map((node) => node.props.id),
    ["mute", "dpe-open-screensharing", "advanced-items", "native-preview"],
  );
  nodes(menuTree, (node) => node.props.id === "dpe-open-screensharing")[0].props.action();
  assert.equal(closed, 1);
  assert.equal(f.calls.at(-1).op, "open-settings");
});

test("Vanilla shortcut alone does not suppress the optimized empty state", () => {
  const View = makeView(
    {
      createElement: (type, props, ...children) => ({ type, props: { ...props, children } }),
      useRef: (value) => ({ current: value }),
      useState: (value) => [value, () => {}],
      useEffect() {},
    },
    () => "",
  );
  const tree = View({
    sources: [{ token: "portal", kind: "portal", name: "Other window", available: true }],
  });
  assert.equal(nodes(tree, (node) => node.props.role === "option").length, 0);
  assert.equal(
    nodes(tree, (node) => node.type === "h3")[0].props.children[0],
    "You don’t have any Optimized capture sources available.",
  );
  assert.match(
    nodes(tree, (node) => node.type === "a")[0].props.href,
    /screensharing.md#choose-a-capture-method$/,
  );
});

test("loading and failure use one centered icon state without contradictory empty copy", () => {
  const View = makeView(
    {
      createElement: (type, props, ...children) => ({ type, props: { ...props, children } }),
      useRef: (value) => ({ current: value }),
      useState: (value) => [value, () => {}],
      useEffect() {},
    },
    () => "",
  );
  for (const [props, kind, role, icon] of [
    [{ loading: true }, "loading", "status", "screen"],
    [{ error: "Capture selection is unavailable." }, "error", "alert", "warning"],
    [{ loading: true, error: "Capture selection is unavailable." }, "error", "alert", "warning"],
  ]) {
    const tree = View(props);
    const states = nodes(tree, (node) => node.props["data-dpe-capture-state"]);
    assert.equal(states.length, 1);
    assert.equal(states[0].props["data-dpe-capture-state"], kind);
    assert.equal(states[0].props.role, role);
    assert.equal(
      nodes(tree, (node) => node.props.className === "dpe-capture-empty-icon")[0].props.name,
      icon,
    );
    assert.equal(nodes(tree, (node) => node.type === "h3").length, 1);
    assert.equal(nodes(tree, (node) => node.type === "a").length, 0);
  }
});

for (const token of ["game-token", "screen-token"]) {
  test(`committed ${token} survives picker unmount until the native request consumes it`, async () => {
    const f = fixture();
    let tree = await f.ready();
    nodes(tree, (node) => Array.isArray(node.props.sources))[0].props.onSelect(token);
    tree = f.render();
    let before;
    f.context.onNativeStart = () => {
      before = f.calls.length;
      f.remount();
    };
    await nodes(tree, (node) => node.type === "NativeButton")[0].props.onClick();
    const cleanup = f.calls.slice(before);
    assert.equal(
      cleanup.some((call) => call.op === "disarm"),
      false,
    );
    assert.equal(
      cleanup.filter((call) => call.op === "picker-close").every((call) => call.handoff === true),
      true,
    );
    assert.ok(cleanup.some((call) => call.op === "picker-close"));
  });
}
test("ordinary picker dismissal clears the uncommitted selection", async () => {
  const f = fixture();
  await f.ready();
  const before = f.calls.length;
  f.remount();
  const cleanup = f.calls.slice(before);
  assert.equal(
    cleanup.some((call) => call.op === "disarm"),
    true,
  );
  assert.equal(
    cleanup.filter((call) => call.op === "picker-close").every((call) => call.handoff === false),
    true,
  );
});
