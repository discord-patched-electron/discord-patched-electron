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
  const nativeContext = {
    $$typeof: Symbol.for("react.context"),
    _currentValue: { appContext: "APP", renderWindow: {}, windowDispatch: {} },
  };
  const React = {
    useContext: (context) => context._currentValue,
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
  return {
    nativeContext,
    renderPin: () =>
      runtime.c.pin.exports.A({ popoutWindowAlwaysOnTop: true, onToggleStayOnTop: () => {} }),
    context,
    requests,
    handlers,
    render,
    expire() {
      for (const fn of timers) fn();
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
