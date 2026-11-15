const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const adapter = require("../../integration/discord-ui/pip-participants.cjs");
const shared = require("../../integration/discord-ui/runtime.cjs")();
function fixture(media = { streams: [], videos: [], activities: [] }) {
  let visit,
    context,
    persistedHidden = false,
    nativeToggles = 0,
    rerenders = 0,
    layout = "normal";
  const timers = new Set(),
    calls = [];
  const target = {
    name: "DISCORD_CHANNEL_CALL_POPOUT",
    [Symbol.for("dpe.pipWindow")]: true,
    closed: false,
    events: {},
    addEventListener(name, fn) {
      this.events[name] = fn;
    },
  };
  const Context = {
    $$typeof: Symbol.for("react.context"),
    _currentValue: { appContext: "APP", renderWindow: {}, windowDispatch: {} },
  };
  context = Context._currentValue;
  const React = {
    useContext: () => context,
    useState: () => [0, () => rerenders++],
    useEffect: (fn) => fn(),
    createElement: (type, props) => ({ type, props }),
    cloneElement: (tree, props, ...children) => ({
      ...tree,
      props: { ...tree.props, ...props, ...(children.length ? { children: children[0] } : {}) },
    }),
  };
  const store = {
    getParticipants: () => [],
    getStreamParticipants: () => media.streams,
    getVideoParticipants: () => media.videos,
    getActivityParticipants: () => media.activities,
    getVoiceParticipantsHidden: () => persistedHidden,
    getLayout: () => layout,
  };
  const actions = {
    selectParticipant(channelId, id) {
      calls.push({ type: "CHANNEL_RTC_SELECT_PARTICIPANT", channelId, id });
    },
    updateLayout(channelId, next, appContext) {
      layout = next;
      calls.push({ type: "CHANNEL_RTC_UPDATE_LAYOUT", channelId, layout: next, appContext });
    },
    toggleVoiceParticipantsHidden(_channel, hidden) {
      nativeToggles++;
      persistedHidden = hidden;
    },
  };
  const view = {
    $$typeof: Symbol.for("react.memo"),
    compare: () => true,
    type: function nativeView({ participants, filteredParticipants, participantsVersion }) {
      store.getVoiceParticipantsHidden();
      return {
        participants,
        filteredParticipants,
        participantsVersion,
        totalNumberOfParticipants: participants.length,
      };
    },
  };
  const menu = {
    A: function nativeMenu(channel) {
      const hidden = store.getVoiceParticipantsHidden(channel);
      return {
        props: {
          id: "no-video-hide",
          checked: !hidden,
          action: () => actions.toggleVoiceParticipantsHidden(channel, !hidden),
        },
      };
    },
  };
  Object.defineProperty(menu, "A", {
    value: menu.A,
    enumerable: true,
    writable: false,
    configurable: false,
  });
  const viewExports = {
    A: function sizingWrapper(props) {
      return React.createElement("div", { children: React.createElement(view.type, props) });
    },
  };
  Object.defineProperty(viewExports, "A", {
    value: viewExports.A,
    enumerable: true,
    writable: false,
    configurable: false,
  });
  const runtime = {
    c: { menu: { exports: menu }, view: { exports: viewExports } },
    m: { view: view.type },
  };
  const ui = {
    ...shared,
    inspect: () => ({ React }),
    watch: (_id, _match, fn) => {
      visit = fn;
    },
  };
  const realm = vm.createContext({
    ui,
    setTimeout: (fn) => {
      timers.add(fn);
      return fn;
    },
    clearTimeout: (fn) => timers.delete(fn),
  });
  const install = (show) =>
    vm.runInContext(`(${adapter.toString()})({showNonVideoParticipants:${show}},ui)`, realm);
  install(false);
  visit({ Context }, runtime);
  visit({ store, actions, user: { getCurrentUser: () => ({ id: "me" }), getUser() {} } }, runtime);
  visit(viewExports, runtime);
  visit(menu, runtime);
  return {
    store,
    actions,
    view,
    target,
    calls,
    install,
    menu: () => runtime.c.menu.exports.A("call"),
    render: (props) =>
      runtime.c.view.exports.A({ channel: { id: "call" }, ...props }).props.children,
    pip() {
      context = { appContext: "POPOUT", renderWindow: target };
    },
    main() {
      context = Context._currentValue;
    },
    flush() {
      for (const fn of timers) {
        timers.delete(fn);
        fn();
      }
    },
    get rerenders() {
      return rerenders;
    },
    get nativeToggles() {
      return nativeToggles;
    },
  };
}
test("portal participant view and checkbox keep PiP visibility independent of regular Discord", () => {
  const voice = { id: "voice" },
    camera = { id: "camera" },
    activity = { id: "activity" },
    popped = { id: "other", isPoppedOut: true };
  const f = fixture({ streams: [], videos: [camera], activities: [activity] });
  const props = {
    participants: [voice, camera, activity, popped],
    filteredParticipants: [voice, camera, activity, popped],
    participantsVersion: 4,
  };
  const original = f.view.type;
  assert.deepEqual(
    f.render(props).props.participants,
    props.participants,
    "The main render keeps native arrays",
  );
  f.pip();
  f.target[Symbol.for("dpe.pipWindow")] = false;
  assert.deepEqual(
    f.render(props).props.participants,
    props.participants,
    "An ordinary Discord popout keeps native participant filtering",
  );
  f.target[Symbol.for("dpe.pipWindow")] = true;
  f.target.windowKey = "DISCORD_CHANNEL_CALL_POPOUT";
  f.target.name = "";
  assert.deepEqual(Array.from(f.render(props).props.participants), [camera, activity]);
  assert.equal(f.menu().props.checked, false);
  f.menu().props.action();
  assert.ok(f.rerenders > 0);
  assert.deepEqual(Array.from(f.render(props).props.participants), [voice, camera, activity]);
  assert.equal(
    f.nativeToggles,
    0,
    "The PiP checkbox never dispatches the persisted regular-call action",
  );
  f.main();
  assert.equal(f.menu().props.checked, true);
  f.menu().props.action();
  assert.equal(f.nativeToggles, 1);
  f.pip();
  assert.equal(f.menu().props.checked, true, "Main-call changes do not affect the PiP toggle");
  f.install(false);
  assert.equal(f.menu().props.checked, true, "Unrelated saves preserve local checkbox changes");
  f.install(true);
  f.install(false);
  assert.equal(f.menu().props.checked, false, "Actual saved changes update open PiP views");
  assert.equal(f.view.type, original, "Settings updates never rewrap the component");
});
test("automatic full PiP view excludes own shares, retains activities and stops after interaction", () => {
  const self = { id: "own", stream: { ownerId: "me" } },
    remote = { id: "remote", stream: { ownerId: "them" } },
    camera = { id: "camera" },
    activity = { id: "activity" };
  for (const [streams, videos, activities, expected, manual] of [
    [[self], [], [], null],
    [[self, remote], [], [], "remote"],
    [[self], [camera], [], "camera"],
    [[], [], [activity], "activity"],
    [[remote], [camera], [], null],
    [[remote], [], [], null, true],
  ]) {
    const f = fixture({ streams, videos, activities });
    const participants = [...streams, ...videos, ...activities];
    f.render({ participants });
    f.flush();
    assert.equal(f.calls.length, 0, "The parent call never auto-focuses itself");
    f.pip();
    f.render({ participants });
    if (manual) f.target.events.pointerdown();
    f.flush();
    assert.equal(f.calls.length, expected ? 2 : 0);
    if (expected) {
      assert.equal(f.calls[0].id, expected);
      assert.equal(f.calls[1].appContext, "POPOUT");
      assert.equal(f.calls[1].layout, "full-screen");
    }
    f.render({ participants });
    f.flush();
    assert.equal(f.calls.length, expected ? 2 : 0, "Renders never undo manual deselection");
  }
});

test("closing auto-focused PiP restores the ordinary popout's previous layout", () => {
  const camera = { id: "camera" },
    f = fixture({ streams: [], videos: [camera], activities: [] });
  f.pip();
  f.render({ participants: [camera] });
  f.flush();
  assert.equal(f.store.getLayout(), "full-screen");
  f.target.events.pagehide();
  assert.equal(f.store.getLayout(), "normal");
  assert.equal(f.calls[2].appContext, "POPOUT");
});

test("a sole PiP tile uses the native focused view without a duplicate participant strip", () => {
  const camera = { id: "camera" },
    f = fixture({ streams: [], videos: [camera], activities: [] });
  f.pip();
  const view = f.render({ participants: [camera], showParticipants: true });
  assert.equal(view.props.selectedParticipant, camera);
  assert.equal(view.props.showParticipants, false);
});
