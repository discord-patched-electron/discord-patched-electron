const assert = require("node:assert/strict");
const { test } = require("node:test");
const vm = require("node:vm");
const adapter = require("../../integration/discord-ui/popout.cjs");
const shared = require("../../integration/discord-ui/runtime.cjs")();

test("stream expansion collapses the native members bar once, respecting opt-out and manual reopen", () => {
  let visit, selected;
  const queued = [],
    calls = [],
    participants = [{ id: "stream1" }, { id: "stream2" }, { id: "camera" }];
  let open = true;
  const store = {
    getSelectedParticipant: () => selected,
    getSelectedParticipantId: () => selected?.id,
    getStreamParticipants: () => participants.slice(0, 2),
    getParticipantsOpen: () => open,
  };
  const actions = {
    selectParticipant(channelId, id) {
      calls.push({ type: "CHANNEL_RTC_SELECT_PARTICIPANT", channelId, id, receiver: this });
      selected = participants.find((participant) => participant.id === id);
      return 42;
    },
    toggleParticipants(channelId, value) {
      calls.push({ channelId, value });
      open = value;
    },
  };
  const ui = {
    ...shared,
    watch: (_key, match, callback) => {
      assert.ok(match("CHANNEL_RTC_SELECT_PARTICIPANT"));
      visit = callback;
    },
  };
  const context = vm.createContext({ ui, queueMicrotask: (fn) => queued.push(fn) });
  const install = (enabled) =>
    vm.runInContext(`(${adapter.toString()})({hideMembers:${enabled}},ui)`, context);
  const flush = () => {
    while (queued.length) queued.shift()();
  };
  install(true);
  visit({ A: actions });
  visit({ A: store });
  const wrapper = actions.selectParticipant;
  assert.equal(actions.selectParticipant("channel", "stream1"), 42);
  flush();
  assert.equal(calls[0].receiver, actions);
  assert.equal(open, false);
  actions.toggleParticipants("channel", true);
  visit({ A: actions });
  install(true);
  flush();
  assert.equal(open, true, "discovery and rerenders never undo Show Members");
  assert.equal(actions.selectParticipant, wrapper, "only one wrapper is installed");
  actions.selectParticipant("channel", "camera");
  flush();
  assert.equal(open, true, "ordinary camera selection is unchanged");
  actions.selectParticipant("channel", "stream1");
  actions.selectParticipant("channel", "camera");
  flush();
  assert.equal(open, true, "stale selections cannot collapse another view");
  actions.selectParticipant("channel", "stream2");
  flush();
  assert.equal(open, false);
  open = true;
  install(false);
  actions.selectParticipant("channel", "stream1");
  flush();
  assert.equal(open, true);
});
