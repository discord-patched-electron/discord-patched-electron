const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const adapter = require("../../integration/discord-ui/call-pip.cjs");
const lifecycle = require("../../integration/discord-ui/popout-lifecycle.cjs");
const shared = require("../../integration/discord-ui/runtime.cjs")();
function fixture() {
  let visit;
  const requests = [],
    timers = new Set();
  let updates = 0;
  const nativeContext = {
    $$typeof: Symbol.for("react.context"),
    _currentValue: { appContext: "APP", renderWindow: {}, windowDispatch: {} },
  };
  const React = {
    useContext: (context) => context._currentValue,
    useState: () => [0, () => updates++],
    useEffect: (fn) => fn(),
    Fragment: "fragment",
    createElement: (type, props, ...children) => ({
      type,
      props: { ...props, ...(children.length ? { children } : {}) },
    }),
    cloneElement: (node, props) => ({ ...node, props: { ...node.props, ...props } }),
  };
  const WindowLaunchIcon = () => null;
  function NativeControl({ popoutOpen, onClosePopout, onOpenPopout, ...props }) {
    return React.createElement("native-button", {
      label: popoutOpen ? "Return to call" : "Pop out",
      iconComponent: WindowLaunchIcon,
      onClick: popoutOpen ? onClosePopout : onOpenPopout,
      ...props,
    });
  }
  const exports = {};
  Object.defineProperty(exports, "A", { value: NativeControl, enumerable: true });
  const runtime = { c: { button: { exports } } };
  const context = vm.createContext({
    URL,
    location: {
      href: "https://canary.discord.com/channels/@me",
      origin: "https://canary.discord.com",
    },
    ui: {
      ...shared,
      inspect: () => ({ React, objects: [nativeContext] }),
      watch: (_id, match, fn) => {
        assert.ok(match(NativeControl.toString() + " WindowLaunchIcon"));
        visit = fn;
      },
    },
    setTimeout: (fn) => {
      timers.add(fn);
      return fn;
    },
    clearTimeout: (fn) => timers.delete(fn),
  });
  context.window = context;
  context.addEventListener = () => {};
  context.open = (url, name, features) => {
    const child = { name, close() {} };
    requests.push({ url, name, features, child });
    return child;
  };
  vm.runInContext(`(${lifecycle})()`, context);
  vm.runInContext(`(${adapter})({icons:{pip:'<svg viewBox="0 0 24 24"></svg>'}},ui)`, context);
  visit(exports, runtime);
  const handlers = {
    handleOpenPopout: () =>
      context.open("/popout", "DISCORD_CHANNEL_CALL_POPOUT", "width=854,height=480"),
  };
  function open() {
    return handlers.handleOpenPopout();
  }
  const render = (extra = {}) =>
    runtime.c.button.exports.A({
      className: "native-spacing",
      onOpenPopout: open,
      onClosePopout: () => "closed",
      popoutOpen: false,
      ...extra,
    });
  const pin = {
    A: function NativePin({ popoutWindowAlwaysOnTop, onToggleStayOnTop }) {
      return { checked: popoutWindowAlwaysOnTop, onClick: onToggleStayOnTop };
    },
  };
  runtime.c.pin = { exports: pin };
  visit(pin, runtime);
  class NativeOverlay {
    constructor(props) {
      this.props = props;
    }
    forceUpdate() {
      updates++;
    }
    handleOpenPopout = () => open();
    renderSettingsIcon = () =>
      React.createElement("settings-cog", { className: "overlay-control" });
    renderPopoutIcon = () =>
      React.createElement(runtime.c.button.exports.A, {
        className: "overlay-control",
        popoutOpen: false,
        onOpenPopout: this.handleOpenPopout,
        onClosePopout: () => {},
      });
    renderBottomRightControls = () =>
      React.createElement(
        React.Fragment,
        null,
        React.createElement("viewers"),
        this.props.settings ? this.renderSettingsIcon() : null,
        this.props.popout ? this.renderPopoutIcon() : null,
        React.createElement("stop"),
      );
  }
  const overlay = {
    A: function Overlay({
      participantOnScreen,
      isMainWindowFocused,
      activeSelfStream,
      width,
      ...props
    }) {
      const node = React.createElement(NativeOverlay, {
        participantOnScreen,
        isMainWindowFocused,
        activeSelfStream,
        width,
        ...props,
      });
      return props.providers
        ? React.createElement("provider", {
            children: [node, { type: "unrelated", props: {} }],
          })
        : node;
    },
  };
  runtime.c.overlay = { exports: overlay };
  visit(overlay, runtime);
  const position = { pipVideoWindow: { position: "top-left" }, getDockedRect() {}, pipWidth() {} };
  visit({ position }, runtime);
  return {
    nativeContext,
    position,
    follow(enabled) {
      vm.runInContext(`(${adapter})({followInAppPosition:${enabled}},ui)`, context);
    },
    renderPin: () =>
      runtime.c.pin.exports.A({ popoutWindowAlwaysOnTop: true, onToggleStayOnTop: () => {} }),
    context,
    requests,
    handlers,
    render,
    renderOverlay: (props = { settings: true, popout: false }) =>
      runtime.c.overlay.exports.A(props),
    expire() {
      for (const fn of timers) fn();
    },
    get updates() {
      return updates;
    },
  };
}
test("call toolbar adds native-styled PiP beside the unchanged popout, opting in only for PiP clicks", () => {
  const f = fixture(),
    tree = f.render(),
    [pip, stock] = tree.props.children;
  assert.equal(tree.type, "fragment");
  assert.equal(pip.props.label, "Picture-in-picture");
  assert.equal(pip.props.className, stock.props.className);
  assert.equal(stock.props.label, "Pop out");
  pip.props.onClick();
  assert.equal(
    f.render().type,
    "native-button",
    "All call toolbars omit the PiP entry while PiP is open",
  );
  f.requests[0].child.closed = true;
  assert.match(f.requests[0].features, /dpe-pip=yes/);
  assert.equal(f.requests[0].child[Symbol.for("dpe.pipWindow")], true);
  stock.props.onClick();
  assert.doesNotMatch(f.requests[1].features, /dpe-pip/);
  assert.equal(f.requests[1].child[Symbol.for("dpe.pipWindow")], false);
  const [disabled, close] = f.render({ popoutOpen: true }).props.children;
  assert.equal(disabled.props.disabled, true);
  assert.equal(close.props.onClick(), "closed");
  disabled.props.onClick();
  assert.equal(f.requests.length, 2);
  const activity = f.render({ onOpenPopout: () => "activity" });
  assert.equal(activity.type, "native-button", "Other popout controls stay native");
});

test("in-app PiP adds its native-styled entry after the cog, preserving other controls and active-PiP suppression", () => {
  const f = fixture(),
    element = f.renderOverlay(),
    overlay = new element.type(element.props),
    controls = overlay.renderBottomRightControls(),
    [viewers, cog, pip, popout, stop] = controls.props.children;
  assert.equal(viewers.type, "viewers");
  assert.equal(cog.type, "settings-cog");
  assert.equal(popout, null);
  assert.equal(stop.type, "stop");
  assert.equal(pip.props.label, "Picture-in-picture");
  assert.equal(pip.props.className, cog.props.className);
  assert.equal(
    f.renderOverlay().type,
    element.type,
    "Keep the native overlay mounted across renders",
  );
  const native = pip.type(pip.props);
  assert.equal(native.type, "native-button", "No second toolbar entry around the overlay control");
  const icon = native.props.iconComponent({ color: "#000000", className: "native-icon" });
  const svg = icon.type(icon.props);
  assert.equal(svg.props.className, "native-icon");
  assert.equal(svg.props.style.color, undefined, "Native overlay icon CSS supplies its color");
  native.props.onClick();
  assert.match(f.requests[0].features, /dpe-pip=yes/);
  assert.equal(overlay.renderBottomRightControls().props.children.length, 4);
  assert.equal(f.render().type, "native-button");
  f.requests[0].child.closed = true;
  assert.equal(overlay.renderBottomRightControls().props.children.length, 5);
  const remote = f.renderOverlay({ settings: false, popout: true });
  const children = new remote.type(remote.props).renderBottomRightControls().props.children;
  assert.equal(children[2].props.label, "Picture-in-picture");
  assert.equal(typeof children[3].props.onOpenPopout, "function");
  const ordinary = children[3].type(children[3].props);
  ordinary.props.onClick();
  assert.doesNotMatch(f.requests[1].features, /dpe-pip/);
});
test("in-app controls survive provider wrappers and native remounts without duplicating PiP", () => {
  const f = fixture();
  const render = () => f.renderOverlay({ providers: true, settings: true, popout: false });
  const tree = render(),
    node = tree.props.children[0];
  assert.equal(tree.props.children[1].type, "unrelated");
  assert.equal(render().props.children[0].type, node.type);
  for (let remount = 0; remount < 3; remount++) {
    const instance = new node.type(node.props);
    for (let rerender = 0; rerender < 3; rerender++) {
      const buttons = instance.renderBottomRightControls().props.children;
      assert.equal(
        buttons.filter((button) => button?.props?.label === "Picture-in-picture").length,
        1,
      );
      assert.equal(buttons[1].type, "settings-cog");
    }
  }
});

test("pop-in refreshes unchanged overlay controls after the native window finishes closing", () => {
  const f = fixture(),
    element = f.renderOverlay({
      settings: true,
      popout: false,
      channel: { id: "call" },
      participantOnScreen: { id: "share" },
    }),
    overlay = new element.type(element.props);
  overlay.componentDidMount();
  const pip = overlay.renderBottomRightControls().props.children[2];
  pip.type(pip.props).props.onClick();
  assert.equal(
    vm.runInContext('globalThis[Symbol.for("dpe.callPiP")].origin.participantId', f.context),
    "share",
  );
  const before = f.updates;
  assert.equal(overlay.renderBottomRightControls().props.children.length, 4);
  f.requests[0].child.closed = true;
  f.expire();
  assert.ok(f.updates > before);
  assert.equal(vm.runInContext('globalThis[Symbol.for("dpe.callPiP")].origin', f.context), null);
  assert.equal(overlay.renderBottomRightControls().props.children.length, 5);
  overlay.componentWillUnmount();
});

test("ordinary overlay popout clicks clear an unfinished PiP request", () => {
  const f = fixture();
  let pending;
  f.handlers.handleOpenPopout = () => {
    pending = () => f.context.open("/popout", "DISCORD_CHANNEL_CALL_POPOUT", "");
  };
  const element = f.renderOverlay({ settings: false, popout: true });
  const [, , pip, stock] = new element.type(element.props).renderBottomRightControls().props
    .children;
  pip.type(pip.props).props.onClick();
  stock.type(stock.props).props.onClick();
  pending();
  assert.doesNotMatch(f.requests[0].features, /dpe-pip/);
});

test("following reads the current native overlay corner at click time and leaves main-call launches on their default", () => {
  for (const corner of ["top-left", "top-right", "bottom-left", "bottom-right"]) {
    const f = fixture();
    f.follow(true);
    const element = f.renderOverlay(),
      overlay = new element.type(element.props);
    const pip = overlay.renderBottomRightControls().props.children[2];
    f.position.pipVideoWindow.position = corner;
    pip.type(pip.props).props.onClick();
    assert.ok(f.requests[0].features.includes(`dpe-pip-corner=${corner}`));
  }
  for (const mode of ["disabled", "hidden", "missing", "unknown", "throwing", "main"]) {
    const f = fixture();
    f.follow(mode !== "disabled");
    if (mode === "hidden") f.position.pipVideoWindow.hidden = true;
    if (mode === "missing") f.position.pipVideoWindow = null;
    if (mode === "unknown") f.position.pipVideoWindow.position = "center";
    if (mode === "throwing")
      Object.defineProperty(f.position, "pipVideoWindow", {
        get() {
          throw Error("closed");
        },
      });
    const element = f.renderOverlay();
    const pip =
      mode === "main"
        ? f.render().props.children[0]
        : new element.type(element.props).renderBottomRightControls().props.children[2];
    (mode === "main" ? pip : pip.type(pip.props)).props.onClick();
    assert.match(f.requests[0].features, /dpe-pip=yes/);
    assert.doesNotMatch(f.requests[0].features, /dpe-pip-corner=/);
  }
});

test("native asynchronous opens consume PiP once; normal clicks, failures and expiry clear the intent", async () => {
  const f = fixture();
  let pending;
  f.handlers.handleOpenPopout = () => {
    pending = () => f.context.open("/popout", "DISCORD_CHANNEL_CALL_POPOUT", "");
  };
  const [pip, stock] = f.render().props.children;
  pip.props.onClick();
  pending();
  pending();
  assert.match(f.requests[0].features, /dpe-pip=yes/);
  assert.doesNotMatch(f.requests[1].features, /dpe-pip/);
  pip.props.onClick();
  stock.props.onClick();
  pending();
  assert.doesNotMatch(f.requests[2].features, /dpe-pip/);
  pip.props.onClick();
  f.expire();
  pending();
  assert.doesNotMatch(f.requests[3].features, /dpe-pip/);
  f.handlers.handleOpenPopout = () => Promise.reject(Error("cancelled"));
  await assert.rejects(pip.props.onClick(), /cancelled/);
  assert.equal(vm.runInContext('globalThis[Symbol.for("dpe.callPiP")].pending', f.context), false);
});

test("the optional native pin toggle remains on normal popouts and is omitted from PiP", () => {
  const f = fixture();
  assert.ok(f.renderPin());
  f.nativeContext._currentValue.appContext = "POPOUT";
  f.nativeContext._currentValue.renderWindow[Symbol.for("dpe.pipWindow")] = true;
  assert.equal(f.renderPin(), null);
  f.nativeContext._currentValue.renderWindow[Symbol.for("dpe.pipWindow")] = false;
  assert.ok(f.renderPin());
});

test("PiP omits its own entry button and the icon follows Discord's interactive color token", () => {
  const f = fixture();
  const [pip] = f.render().props.children;
  const icon = pip.props.iconComponent({ color: "#000000" });
  assert.match(icon.props.style.color, /var\(--interactive-normal/);
  f.nativeContext._currentValue.renderWindow[Symbol.for("dpe.pipWindow")] = true;
  assert.equal(f.render().type, "native-button");
});
