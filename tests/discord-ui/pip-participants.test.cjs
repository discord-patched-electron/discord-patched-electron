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
    layout = "normal",
    selected = null,
    membersOpen = false;
  const timers = new Set(),
    changes = new Set(),
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
  let registeredWindow = target;
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
    getSelectedParticipant: () => selected,
    getParticipantsOpen: () => membersOpen,
    addChangeListener: (fn) => changes.add(fn),
    removeChangeListener: (fn) => changes.delete(fn),
  };
  const actions = {
    selectParticipant(channelId, id) {
      selected = id == null ? null : { id };
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
    toggleParticipants(channelId, participantsOpen) {
      membersOpen = participantsOpen;
      calls.push({ type: "CHANNEL_RTC_UPDATE_PARTICIPANTS_OPEN", channelId, participantsOpen });
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
      return React.createElement("div", {
        style: { width: "100%", height: "100%", display: "flex" },
        children: React.createElement(view.type, props),
      });
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
  class NativeCall {
    constructor(props) {
      this.props = props;
    }
    get inPopout() {
      return this.props.appContext === "POPOUT";
    }
    get popoutOpen() {
      return !!this.props.popoutWindow && !this.props.popoutWindow.closed;
    }
    get viewProperties() {
      return {
        canFullscreen: true,
        canStayOnTop: true,
        canPopout: this.props.layout !== "full-screen",
      };
    }
    handleClosePopout = () => {
      target.closed = true;
    };
    renderBottomRight() {
      return this.viewProperties.canPopout
        ? {
            label: this.popoutOpen ? "Return to Discord" : "Pop out",
            onClick: this.handleClosePopout,
          }
        : null;
    }
  }
  const callExports = {
    default: function nativeCall({ channel, popoutType }) {
      const layout = store.getLayout(channel.id),
        selectedParticipant = store.getSelectedParticipant(channel.id);
      const getMode = () => "video";
      const call = React.createElement(NativeCall, {
        channel,
        layout,
        selectedParticipant,
        mode: getMode(),
        appContext: context.appContext,
        popoutWindow: registeredWindow,
        participants: store.getParticipants(channel.id),
        guildRoomVisible: true,
        callContainerDimensions: { width: 320, height: 180 },
        popoutType,
      });
      return React.createElement("Analytics", {
        children: React.createElement("PageContext", {
          children: React.createElement("CallProviders", {
            children: [call, React.createElement("Footer", {})],
          }),
        }),
      });
    },
  };
  runtime.c.call = { exports: callExports };
  const realm = vm.createContext({
    ui,
    target,
    setTimeout: (fn) => {
      timers.add(fn);
      return fn;
    },
    clearTimeout: (fn) => timers.delete(fn),
  });
  const install = (show, hide = true) =>
    vm.runInContext(
      `(${adapter.toString()})({showNonVideoParticipants:${show},hideMembers:${hide}},ui)`,
      realm,
    );
  install(false);
  visit({ Context }, runtime);
  visit({ store, actions, user: { getCurrentUser: () => ({ id: "me" }), getUser() {} } }, runtime);
  visit(viewExports, runtime);
  visit(menu, runtime);
  visit(callExports, runtime);
  return {
    store,
    actions,
    view,
    target,
    calls,
    install,
    setMembers(open) {
      membersOpen = open;
    },
    setRegisteredWindow(value) {
      registeredWindow = value;
    },
    menu: () => runtime.c.menu.exports.A("call"),
    renderCall: () => {
      const tree = runtime.c.call.exports.default({ channel: { id: "call" }, popoutType: "voice" });
      assert.equal(tree.type, "Analytics");
      assert.equal(tree.props.children.type, "PageContext");
      const children = tree.props.children.props.children.props.children;
      assert.equal(children[1].type, "Footer");
      return children[0];
    },
    renderMeasured: (props) => runtime.c.view.exports.A({ channel: { id: "call" }, ...props }),
    origin(participantId) {
      vm.runInContext(
        `globalThis[Symbol.for("dpe.callPiP")]={activeWindow:target,origin:{channelId:"call",participantId:${JSON.stringify(participantId)}}}`,
        realm,
      );
    },
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
    changed() {
      for (const fn of changes) fn();
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
  assert.equal(
    f.menu(),
    null,
    "DPE-disabled non-video participants cannot be enabled from PiP's menu",
  );
  f.install(true);
  f.menu().props.action();
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
  f.install(true);
  assert.equal(f.menu().props.checked, true, "Unrelated saves preserve local checkbox changes");
  const staleMenu = f.menu();
  f.install(false);
  staleMenu.props.action();
  assert.equal(f.menu(), null, "Actual saved changes remove the disabled control");
  assert.deepEqual(Array.from(f.render(props).props.participants), [camera, activity]);
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
  assert.equal(f.calls[2].id, null);
  assert.equal(f.calls[3].appContext, "POPOUT");
});

test("PiP opened from the in-app overlay focuses its sole media without waiting for the participant view", () => {
  const stream = { id: "share", stream: { ownerId: "other" } };
  const f = fixture({ streams: [stream], videos: [], activities: [] });
  assert.equal(f.renderCall().props.guildRoomVisible, true);
  f.flush();
  assert.equal(f.calls.length, 0);
  f.pip();
  assert.equal(f.renderCall().props.guildRoomVisible, false);
  f.flush();
  assert.equal(f.calls[0].id, "share");
  assert.equal(f.calls[1].layout, "full-screen");
  f.target.events.pagehide();
  f.main();
  assert.equal(f.renderCall().props.guildRoomVisible, true);
});

test("an empty fullscreen layout still selects sole media, while existing fullscreen selections survive", () => {
  for (const existing of [null, "manual"]) {
    const camera = { id: "camera" },
      f = fixture({ streams: [], videos: [camera], activities: [] });
    f.actions.updateLayout("call", "full-screen", "POPOUT");
    if (existing) f.actions.selectParticipant("call", existing);
    const before = f.calls.length;
    f.pip();
    f.renderCall();
    f.flush();
    assert.equal(f.calls.length, before + (existing ? 0 : 1));
    if (!existing) assert.equal(f.calls.at(-1).id, "camera");
  }
});

test("sole media arriving after the outer PiP call view mounts still receives automatic focus", () => {
  const media = { streams: [], videos: [], activities: [] },
    f = fixture(media);
  f.pip();
  f.renderCall();
  f.flush();
  assert.equal(f.calls.length, 0);
  media.activities.push({ id: "activity" });
  f.changed();
  f.flush();
  assert.equal(f.calls[0].id, "activity");
  assert.equal(f.calls[1].layout, "full-screen");
});

test("an explicit in-app PiP media target wins among multiple media, excluding the user's own share", () => {
  for (const id of ["remote", "activity", "own"]) {
    const f = fixture({
      streams: [
        { id: "remote", stream: { ownerId: "them" } },
        { id: "own", stream: { ownerId: "me" } },
      ],
      videos: [{ id: "camera" }],
      activities: [{ id: "activity" }],
    });
    f.pip();
    f.origin(id);
    f.renderCall();
    f.flush();
    assert.equal(f.calls.length, id === "own" ? 0 : 2);
    if (id !== "own") assert.equal(f.calls[0].id, id);
  }
});

test("a sole PiP tile uses the native focused view without a duplicate participant strip", () => {
  const camera = { id: "camera" },
    f = fixture({ streams: [], videos: [camera], activities: [] });
  f.pip();
  const view = f.render({ participants: [camera], showParticipants: true });
  assert.equal(view.props.selectedParticipant, camera);
  assert.equal(view.props.showParticipants, false);
});

test("the native measured PiP wrapper can shrink below media minimum size without changing normal calls", () => {
  const f = fixture(),
    props = { participants: [] };
  assert.equal(f.renderMeasured(props).props.style.minHeight, undefined);
  f.pip();
  const style = f.renderMeasured(props).props.style;
  assert.equal(style.minHeight, 0);
  assert.equal(style.minWidth, 0);
  assert.equal(style.height, "100%");
  assert.equal(style.display, "flex");
});

test("PiP minimizes members initially and on selection changes while honoring manual Show Members until then", () => {
  const f = fixture({
    streams: [{ id: "share", stream: { ownerId: "other" } }],
    videos: [{ id: "camera" }],
    activities: [{ id: "activity" }],
  });
  f.pip();
  f.setMembers(true);
  f.renderCall();
  f.flush();
  assert.equal(f.store.getParticipantsOpen(), false);
  for (const id of ["share", null, "share", "camera", "activity"]) {
    f.setMembers(true);
    f.changed();
    f.flush();
    assert.equal(
      f.store.getParticipantsOpen(),
      true,
      "Show Members is respected without a selection change",
    );
    f.actions.selectParticipant("call", id);
    f.changed();
    f.flush();
    assert.equal(f.store.getParticipantsOpen(), false);
  }
  f.install(false, false);
  f.setMembers(true);
  f.actions.selectParticipant("call", "camera");
  f.renderCall();
  f.flush();
  assert.equal(f.store.getParticipantsOpen(), true, "The opt-out retains the member bar");
});

test("PiP fullscreen keeps the native pop-in control and regular Discord retains its own visibility rules", () => {
  const f = fixture();
  f.actions.updateLayout("call", "full-screen", "POPOUT");
  let tree = f.renderCall();
  assert.equal(new tree.type(tree.props).renderBottomRight(), null);
  f.pip();
  tree = f.renderCall();
  const view = new tree.type(tree.props);
  assert.equal(f.renderCall().type, tree.type, "Native class state survives rerenders");
  const control = view.renderBottomRight();
  assert.equal(control.label, "Return to Discord");
  control.onClick();
  assert.equal(f.target.closed, true);
});

test("manual deselection of a sole tile stays deselected", () => {
  const camera = { id: "camera" },
    f = fixture({ streams: [], videos: [camera], activities: [] });
  f.pip();
  f.render({ participants: [camera] });
  f.flush();
  f.target.events.pointerdown();
  f.actions.selectParticipant("call", null);
  const view = f.render({
    participants: [camera],
    selectedParticipant: null,
    showParticipants: true,
  });
  assert.equal(view.props.selectedParticipant, null);
});

test("PiP return is present before WindowStore registers the guest and after registration", () => {
  const f = fixture();
  f.setRegisteredWindow(null);
  f.pip();
  for (const layout of ["normal", "no-chat", "full-screen", "normal"]) {
    f.actions.updateLayout("call", layout, "POPOUT");
    const tree = f.renderCall(),
      view = new tree.type(tree.props);
    assert.equal(tree.props.popoutWindow, f.target);
    assert.equal(view.renderBottomRight().label, "Return to Discord", layout);
  }
  f.setRegisteredWindow(f.target);
  const tree = f.renderCall();
  assert.equal(new tree.type(tree.props).renderBottomRight().label, "Return to Discord");
});

test("manual media callbacks minimize members even when the selected ID stays the same", () => {
  const camera = { id: "camera" },
    f = fixture({ streams: [], videos: [camera], activities: [] });
  f.pip();
  f.renderCall();
  f.flush();
  f.setMembers(true);
  const view = f.render({
    participants: [camera],
    selectedParticipant: camera,
    onSelectParticipant: () => "selected",
    onFullscreenParticipant: () => "expanded",
  });
  assert.equal(view.props.onSelectParticipant(camera), "selected");
  f.flush();
  assert.equal(f.store.getParticipantsOpen(), false);
  f.setMembers(true);
  assert.equal(view.props.onFullscreenParticipant(camera), "expanded");
  f.flush();
  assert.equal(f.store.getParticipantsOpen(), false);
  f.setMembers(true);
  f.actions.updateLayout("call", "normal", "POPOUT");
  f.changed();
  f.flush();
  assert.equal(f.store.getParticipantsOpen(), false);
});
