const { test } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { wrap, installVoice } = require("../../integration/audio/wayland-audio.cjs");
function fixture(t, pickerUI, autoDetect, disableAudio) {
  const calls = [],
    children = [];
  let ended;
  const native = {
    setTransportOptions(o) {
      assert.equal(this, native);
      calls.push(o);
    },
    setDesktopSource() {},
    setDesktopSourceWithOptions() {},
    clearDesktopSource() {},
    destroy() {},
    setSelfMute(value) {
      assert.equal(this, native);
      calls.push({ mute: value });
    },
    setOnDesktopSourceEnded(cb) {
      ended = cb;
    },
  };
  function spawnHelper() {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.stdin = new EventEmitter();
    child.messages = [];
    child.stdin.write = (line) => child.messages.push(JSON.parse(line));
    child.stdin.end = () => {
      child.closed = true;
    };
    child.kill = () => {
      child.killed = true;
    };
    children.push(child);
    return child;
  }
  const disabled = [],
    warnings = [];
  const adapter = wrap(native, {
    spawnHelper,
    warn: (message) => warnings.push(message),
    pickerUI,
    autoDetect,
    disableAudio: () => {
      disabled.push(true);
      return disableAudio ? disableAudio() : true;
    },
  });
  t.after(() => adapter.destroy());
  const ready = (child, pid, generation = child.messages.at(-1).generation) =>
    child.stdout.emit(
      "data",
      Buffer.from(JSON.stringify({ generation, pid, state: "ready" }) + "\n"),
    );
  return { adapter, native, calls, children, ready, disabled, warnings, end: () => ended() };
}
test("rejected native sources silence the old app and close its helper", (t) => {
  for (const method of ["setDesktopSource", "setDesktopSourceWithOptions"]) {
    const f = fixture(t);
    f.adapter.setDesktopSource("screen:0:0");
    f.adapter.setTransportOptions({ soundsharePid: 1 });
    const first = f.children[0];
    f.ready(first, 2500);
    f.native[method] = () => {
      throw new Error("source disappeared");
    };
    assert.throws(() => f.adapter[method]("missing"), /source disappeared/);
    assert.equal(first.closed, true);
    assert.equal(f.calls.at(-1).soundsharePid, 0);
    f.ready(first, 2500);
    assert.equal(f.calls.at(-1).soundsharePid, 0);
    f.adapter.destroy();
  }
});
test("Wayland never forwards the system sentinel before the selected mix is ready", (t) => {
  const f = fixture(t);
  f.adapter.setDesktopSourceWithOptions({
    sourceId: "screen:0:0",
    type: "screen",
  });
  f.adapter.setTransportOptions({
    soundsharePid: 1,
    soundshareLoopback: true,
    encryptionKey: "untouched",
  });
  assert.equal(f.calls.at(-1).soundsharePid, 0);
  assert.equal(f.calls.find((x) => x.encryptionKey)?.encryptionKey, "untouched");
  f.ready(f.children[0], 2500);
  assert.equal(f.calls.at(-1).soundsharePid, 2500);
  f.adapter.setSelfMute(true);
  assert.deepEqual(f.calls.at(-1), { mute: true });
  f.adapter.setTransportOptions({ soundsharePid: 0 });
  assert.equal(f.calls.at(-1).soundsharePid, 0);
  assert.equal(f.children[0].closed, true);
  f.adapter.destroy();
});
test("late helper responses cannot restore audio after switching or ending a source", (t) => {
  const f = fixture(t);
  f.adapter.setDesktopSource("screen:0:0");
  f.adapter.setTransportOptions({ soundsharePid: 1 });
  const first = f.children[0],
    oldGeneration = first.messages.at(-1).generation;
  f.adapter.setDesktopSource("screen:1:0");
  f.ready(first, 2500, oldGeneration);
  assert.equal(f.calls.at(-1).soundsharePid, 0);
  f.ready(first, 2600);
  assert.equal(f.calls.at(-1).soundsharePid, 2600);
  f.end();
  assert.equal(f.calls.at(-1).soundsharePid, 0);
  f.ready(first, 2600);
  assert.equal(f.calls.at(-1).soundsharePid, 0);
  f.adapter.destroy();
});
test("native picker callbacks stop capture while choosing and after cancellation", (t) => {
  const previous = process.env.DPE_APP_AUDIO;
  const previousLatency = process.env.PULSE_LATENCY_MSEC;
  delete process.env.PULSE_LATENCY_MSEC;
  delete process.env.DPE_APP_AUDIO;
  try {
    let selected, cancelled, failed;
    let forwarded = 0;
    const voice = {
      presentNativeScreenSharePicker() {
        return 17;
      },
      setNativeScreenSharePickerCallbacks(a, b, c) {
        selected = a;
        cancelled = b;
        failed = c;
      },
      releaseNativeDesktopVideoSourcePickerStream() {},
    };
    installVoice(voice);
    assert.equal(process.env.PULSE_LATENCY_MSEC, "40");
    process.env.PULSE_LATENCY_MSEC = "73";
    installVoice({ ...voice });
    assert.equal(process.env.PULSE_LATENCY_MSEC, "73");
    voice.setNativeScreenSharePickerCallbacks(
      () => forwarded++,
      () => forwarded++,
      () => forwarded++,
    );
    const f = fixture(t);
    const idle = fixture(t);
    const microphoneOptions = {
      echoCancellation: true,
      noiseCancellation: true,
    };
    idle.adapter.setTransportOptions(microphoneOptions);
    assert.equal(idle.calls[0], microphoneOptions);
    f.adapter.setDesktopSource("screen:0:0");
    f.adapter.setTransportOptions({ soundsharePid: 1 });
    f.ready(f.children[0], 2500);
    assert.equal(voice.presentNativeScreenSharePicker("window"), 17);
    assert.equal(idle.calls.length, 1);
    assert.equal(idle.children.length, 0);
    assert.equal(f.calls.at(-1).soundsharePid, 0);
    selected(false, null);
    assert.equal(forwarded, 1);
    f.ready(f.children.at(-1), 2600);
    assert.equal(f.calls.at(-1).soundsharePid, 2600);
    cancelled(false);
    assert.equal(f.calls.at(-1).soundsharePid, 0);
    assert.equal(forwarded, 2);
    failed("cancelled");
    assert.equal(forwarded, 3);
    f.adapter.destroy();
    idle.adapter.destroy();
  } finally {
    if (previousLatency === undefined) delete process.env.PULSE_LATENCY_MSEC;
    else process.env.PULSE_LATENCY_MSEC = previousLatency;
    if (previous === undefined) delete process.env.DPE_APP_AUDIO;
    else process.env.DPE_APP_AUDIO = previous;
  }
});

test("explicit Wayland opt-out leaves picker methods and buffer settings alone", () => {
  const previous = process.env.DPE_APP_AUDIO;
  const latency = process.env.PULSE_LATENCY_MSEC;
  process.env.DPE_APP_AUDIO = "0";
  try {
    const voice = {};
    installVoice(voice);
    assert.deepEqual(voice, {});
    assert.equal(process.env.PULSE_LATENCY_MSEC, latency);
    const { wrap: entry } = require("../../integration/audio/app-audio.cjs");
    assert.equal(entry(voice, { wayland: true }), voice);
  } finally {
    if (previous === undefined) delete process.env.DPE_APP_AUDIO;
    else process.env.DPE_APP_AUDIO = previous;
  }
});

test("fragmented helper messages and invalid PIDs never become system capture", (t) => {
  const f = fixture(t);
  f.adapter.setDesktopSource("screen:0:0");
  f.adapter.setTransportOptions({ soundsharePid: 1 });
  const child = f.children[0];
  const generation = child.messages.at(-1).generation;
  const message = JSON.stringify({ generation, state: "ready", pid: 2500 });
  child.stdout.emit("data", Buffer.from(message.slice(0, 10)));
  assert.equal(f.calls.at(-1).soundsharePid, 0);
  child.stdout.emit("data", Buffer.from(message.slice(10) + "\n"));
  assert.equal(f.calls.at(-1).soundsharePid, 2500);
  for (const pid of [0, 1, -1, 1.5, "2500", null, Number.MAX_SAFE_INTEGER + 1]) {
    f.ready(child, 2500);
    f.ready(child, pid);
    assert.equal(f.calls.at(-1).soundsharePid, 0);
  }
  child.stdout.emit("data", Buffer.from("not-json\nnull\n[]\n"));
  assert.equal(f.calls.at(-1).soundsharePid, 0);
  f.ready(child, 2700);
  assert.equal(f.calls.at(-1).soundsharePid, 2700);
});

test("helper exit or failure silences capture and ignores stale messages", (t) => {
  for (const event of ["exit", "error"]) {
    const f = fixture(t);
    f.adapter.setDesktopSource("screen:0:0");
    f.adapter.setTransportOptions({ soundsharePid: 1 });
    const child = f.children[0];
    f.ready(child, 2500);
    child.emit(event, new Error("fixture failure"));
    assert.equal(f.calls.at(-1).soundsharePid, 0);
    f.ready(child, 2700);
    assert.equal(f.calls.at(-1).soundsharePid, 0);
  }
});

test("share setup avoids redundant native audio resets and passes voice options unchanged", (t) => {
  const f = fixture(t);
  f.adapter.setDesktopSource("screen:0:0");
  assert.equal(f.calls.length, 0);
  f.adapter.setTransportOptions({ soundsharePid: 1 });
  assert.equal(f.calls.length, 1);
  f.ready(f.children[0], 2500);
  const options = { echoCancellation: true, noiseCancellation: true };
  f.adapter.setTransportOptions(options);
  assert.equal(f.calls.at(-1), options);
  const before = f.calls.length;
  f.adapter.clearDesktopSource();
  assert.equal(f.calls.length, before + 1);
  f.adapter.clearDesktopSource();
  f.adapter.destroy();
  assert.equal(f.calls.length, before + 1);
});

test("inline picker binds replies to the source and closes when the helper stops", (t) => {
  const shown = [];
  let closed = 0;
  const f = fixture(t, {
    available: () => true,
    show(options, reply) {
      shown.push({ options, reply });
      return () => closed++;
    },
  });
  f.adapter.setDesktopSource("screen:0:0");
  f.adapter.setTransportOptions({ soundsharePid: 1 });
  const child = f.children[0];
  const { generation, picker } = child.messages.at(-1);
  assert.equal(picker, "inline");
  const options = [{ id: "200", label: "音楽", detail: "<b>Track</b>" }];
  const message = Buffer.from(
    JSON.stringify({ generation, state: "choosing", request: 3, options }) + "\n",
  );
  const split = message.indexOf(Buffer.from("音")) + 1;
  child.stdout.emit("data", message.subarray(0, split));
  child.stdout.emit("data", message.subarray(split));
  assert.deepEqual(shown[0].options, options);
  shown[0].reply("200");
  assert.deepEqual(child.messages.at(-1), { generation, request: 3, selection: "200" });
  f.adapter.setDesktopSource("screen:1:0");
  const count = child.messages.length;
  shown[0].reply("system");
  assert.equal(child.messages.length, count);
  assert.equal(closed, 1);
  child.stdout.emit(
    "data",
    Buffer.from(
      JSON.stringify({
        generation: child.messages.at(-1).generation,
        state: "choosing",
        request: 4,
        options,
      }) + "\n",
    ),
  );
  f.end();
  assert.equal(closed, 2);
});

test("inline rendering failure requests the native picker without enabling audio", (t) => {
  const f = fixture(t, {
    available: () => true,
    show() {
      throw new Error("no DOM");
    },
  });
  f.adapter.setDesktopSource("screen:0:0");
  f.adapter.setTransportOptions({ soundsharePid: 1 });
  const child = f.children[0];
  const { generation } = child.messages.at(-1);
  child.stdout.emit(
    "data",
    Buffer.from(JSON.stringify({ generation, state: "choosing", request: 1, options: [] }) + "\n"),
  );
  assert.deepEqual(child.messages.at(-1), { generation, request: 1, selection: "fallback" });
  assert.equal(f.calls.at(-1).soundsharePid, 0);
});

test("auto-detection preference is re-read for each new share", (t) => {
  let automatic = false;
  const f = fixture(t, undefined, () => automatic);
  f.adapter.setDesktopSource("screen:0:0");
  f.adapter.setTransportOptions({ soundsharePid: 200 });
  assert.equal(f.children[0].messages.at(-1).autoDetect, false);
  automatic = true;
  f.adapter.setDesktopSource("screen:1:0");
  assert.equal(f.children[0].messages.at(-1).autoDetect, true);
});

test("refresh updates the mounted picker and replaces its request callback", (t) => {
  const shown = [],
    updates = [];
  let closed = 0;
  const f = fixture(t, {
    available: () => true,
    show(options, reply) {
      shown.push({ options, reply });
      const close = () => closed++;
      close.update = (options, reply) => updates.push({ options, reply });
      return close;
    },
  });
  f.adapter.setDesktopSource("screen:0:0");
  f.adapter.setTransportOptions({ soundsharePid: 1 });
  const child = f.children[0];
  const { generation } = child.messages.at(-1);
  const choose = (request, options = []) =>
    child.stdout.emit(
      "data",
      Buffer.from(JSON.stringify({ generation, state: "choosing", request, options }) + "\n"),
    );
  choose(1);
  shown[0].reply("refresh");
  assert.deepEqual(child.messages.at(-1), { generation, request: 1, selection: "refresh" });
  choose(2, [{ id: "200", label: "Music" }]);
  assert.equal(shown.length, 1);
  assert.equal(closed, 0);
  assert.equal(updates.length, 1);
  const count = child.messages.length;
  shown[0].reply("200");
  assert.equal(child.messages.length, count, "old request callback is stale");
  updates[0].reply("200");
  assert.deepEqual(child.messages.at(-1), { generation, request: 2, selection: "200" });
  f.ready(child, 2500, generation);
  assert.equal(closed, 1);
});

test("cancelling a refresh cannot reopen the picker when a late choice arrives", (t) => {
  const shown = [];
  const f = fixture(t, {
    available: () => true,
    show(options, reply) {
      shown.push(reply);
      return () => {};
    },
  });
  f.adapter.setDesktopSource("screen:0:0");
  f.adapter.setTransportOptions({ soundsharePid: 1 });
  const child = f.children[0];
  const { generation } = child.messages.at(-1);
  const choose = (request) =>
    child.stdout.emit(
      "data",
      Buffer.from(JSON.stringify({ generation, state: "choosing", request, options: [] }) + "\n"),
    );
  choose(1);
  shown[0]("refresh");
  shown[0]("none");
  choose(2);
  assert.equal(shown.length, 1);
  assert.deepEqual(child.messages.at(-1), { generation, request: 1, selection: "none" });
  assert.equal(
    child.closed,
    true,
    "cancelled helper cannot continue capturing or reopening the picker",
  );
  assert.equal(f.disabled.length, 1, "the native Share Stream Audio preference is turned off once");
  assert.equal(f.calls.at(-1).soundsharePid, 0);
  f.ready(child, 2500, generation);
  shown[0]("200");
  shown[0]("none");
  assert.equal(f.disabled.length, 1, "a stale picker cannot change current preferences");
  assert.equal(f.calls.at(-1).soundsharePid, 0);
  f.adapter.setTransportOptions({ soundsharePid: 1 });
  assert.equal(f.children.length, 2, "re-enabling audio creates a fresh selection");
  assert.equal(f.children[1].messages.at(-1).forcePicker, true);
  f.ready(f.children[1], 2600);
  assert.equal(f.calls.at(-1).soundsharePid, 2600);
});

test("cancellation stays silent when Discord's preference action is unavailable or rejects", async (t) => {
  for (const disableAudio of [
    () => false,
    () => Promise.reject(Error("unavailable")),
    () => {
      throw Error("unavailable");
    },
  ]) {
    let reply;
    const f = fixture(
      t,
      {
        available: () => true,
        show(_options, callback) {
          reply = callback;
          return () => {};
        },
      },
      undefined,
      disableAudio,
    );
    f.adapter.setDesktopSource("screen:0:0");
    f.adapter.setTransportOptions({ soundsharePid: 1 });
    const child = f.children[0],
      { generation } = child.messages.at(-1);
    child.stdout.emit(
      "data",
      Buffer.from(
        JSON.stringify({ generation, state: "choosing", request: 1, options: [] }) + "\n",
      ),
    );
    reply("none");
    await new Promise(setImmediate);
    assert.equal(child.closed, true);
    assert.equal(f.calls.at(-1).soundsharePid, 0);
    assert.equal(f.warnings.length, 1);
  }
});

test("window audio re-enable requests a chooser even with a valid native PID hint", (t) => {
  for (const sourceFirst of [false, true]) {
    const shown = [];
    const f = fixture(
      t,
      {
        available: () => true,
        show(options, reply) {
          shown.push({ options, reply });
          return () => {};
        },
      },
      () => true,
    );
    const selectVideo = () =>
      f.adapter.setDesktopSourceWithOptions({ sourceId: "window-1", type: "window" });
    if (sourceFirst) selectVideo();
    f.adapter.setTransportOptions({ soundsharePid: 200, soundshareEventDriven: true });
    if (!sourceFirst) selectVideo();
    const initial = f.children[0];
    assert.equal(
      initial.messages.at(-1).forcePicker,
      false,
      "initial setup preserves automatic detection in either call order",
    );
    assert.equal(initial.messages.at(-1).autoDetect, true);
    f.ready(initial, 2500);
    const before = f.calls.length;
    f.adapter.setTransportOptions({ soundsharePid: 0 });
    assert.equal(f.calls.length, before + 1, "disabling emits one native reset");
    assert.equal(initial.closed, true);
    f.adapter.setTransportOptions({
      soundsharePid: 200,
      soundshareEventDriven: true,
      encryptionKey: "unchanged",
    });
    const reopened = f.children[1],
      configuration = reopened.messages.at(-1);
    assert.equal(configuration.forcePicker, true);
    assert.equal(configuration.autoDetect, true, "the saved automatic preference is not changed");
    assert.equal(configuration.hint, 200, "the hint still ranks suggestions");
    assert.equal(f.calls.at(-1).soundsharePid, 0);
    assert.equal(f.calls.at(-1).encryptionKey, "unchanged");
    f.ready(initial, 2800);
    assert.equal(f.calls.at(-1).soundsharePid, 0, "late old audio stays silent");
    reopened.stdout.emit(
      "data",
      Buffer.from(
        JSON.stringify({
          generation: configuration.generation,
          state: "choosing",
          request: 1,
          options: [{ id: "300", label: "Music" }],
        }) + "\n",
      ),
    );
    assert.equal(shown.length, 1);
    const count = reopened.messages.length;
    f.adapter.setTransportOptions({ soundsharePid: 200 });
    selectVideo();
    assert.equal(
      reopened.messages.length,
      count,
      "repeated transport/source setters keep the same chooser request",
    );
    shown[0].reply("300");
    assert.equal(reopened.messages.at(-1).selection, "300");
    f.ready(reopened, 2600, configuration.generation);
    assert.equal(f.calls.at(-1).soundsharePid, 2600);
    const voice = { echoCancellation: true, noiseCancellation: true };
    f.adapter.setTransportOptions(voice);
    assert.equal(f.calls.at(-1), voice, "voice processing options pass through unchanged");
    f.adapter.destroy();
  }
});

test("enabling a share that started without audio is an explicit choice", (t) => {
  for (const sourceFirst of [false, true]) {
    const f = fixture(t, undefined, () => true);
    if (sourceFirst) f.adapter.setDesktopSource("window:1:0", false, "window");
    f.adapter.setTransportOptions({ soundsharePid: 0 });
    if (!sourceFirst) f.adapter.setDesktopSource("window:1:0", false, "window");
    assert.equal(f.children.length, 0);
    f.adapter.setTransportOptions({ soundsharePid: 200 });
    assert.equal(f.children[0].messages.at(-1).forcePicker, true);
    assert.equal(f.children[0].messages.at(-1).autoDetect, true);
    f.adapter.destroy();
  }
});

test("new video sources restore automatic preference and discard old manual replies", (t) => {
  let reply;
  const f = fixture(
    t,
    {
      available: () => true,
      show(_options, callback) {
        reply = callback;
        return () => {};
      },
    },
    () => true,
  );
  f.adapter.setDesktopSourceWithOptions({ sourceId: "window-1", type: "window" });
  f.adapter.setTransportOptions({ soundsharePid: 200 });
  f.ready(f.children[0], 2500);
  f.adapter.setTransportOptions({ soundsharePid: 0 });
  f.adapter.setTransportOptions({ soundsharePid: 200 });
  const child = f.children[1],
    generation = child.messages.at(-1).generation;
  child.stdout.emit(
    "data",
    Buffer.from(JSON.stringify({ generation, state: "choosing", request: 1, options: [] }) + "\n"),
  );
  f.adapter.setTransportOptions({ soundsharePid: 300 });
  f.adapter.setDesktopSourceWithOptions({ sourceId: "window-2", type: "window" });
  assert.equal(child.messages.at(-1).forcePicker, false);
  assert.equal(child.messages.at(-1).hint, 300);
  assert.equal(child.messages.at(-1).autoDetect, true);
  const count = child.messages.length;
  reply("system");
  f.ready(child, 2500, generation);
  assert.equal(child.messages.length, count);
  assert.equal(f.calls.at(-1).soundsharePid, 0);
  f.ready(child, 2600);
  assert.equal(f.calls.at(-1).soundsharePid, 2600);
});

test("native picker pending and callback order do not turn a new capture into a re-enable", (t) => {
  const previous = process.env.DPE_AUDIO_AUTO_DETECT;
  process.env.DPE_AUDIO_AUTO_DETECT = "0"; // No portal monitor in this fixture.
  t.after(() => {
    if (previous === undefined) delete process.env.DPE_AUDIO_AUTO_DETECT;
    else process.env.DPE_AUDIO_AUTO_DETECT = previous;
  });
  let selected, cancelled;
  const voice = {
    presentNativeScreenSharePicker() {},
    setNativeScreenSharePickerCallbacks(a, b) {
      selected = a;
      cancelled = b;
    },
    releaseNativeDesktopVideoSourcePickerStream() {},
  };
  installVoice(voice);
  voice.setNativeScreenSharePickerCallbacks(
    () => {},
    () => {},
    () => {},
  );
  t.after(() => voice.releaseNativeDesktopVideoSourcePickerStream());
  for (const sourceFirst of [true, false]) {
    const f = fixture(t, undefined, () => true),
      idle = fixture(t, undefined, () => true);
    const options = { echoCancellation: true };
    idle.adapter.setTransportOptions(options);
    f.adapter.setDesktopSourceWithOptions({ sourceId: "window-1", type: "window" });
    f.adapter.setTransportOptions({ soundsharePid: 200 });
    const old = f.children[0];
    f.ready(old, 2500);
    voice.presentNativeScreenSharePicker();
    assert.equal(old.closed, true);
    f.adapter.setTransportOptions({ soundsharePid: 0 });
    f.adapter.setTransportOptions({ soundsharePid: 300 });
    assert.equal(f.children.length, 1, "audio waits while the native video picker is pending");
    const selectVideo = () =>
      f.adapter.setDesktopSourceWithOptions({ sourceId: "window-2", type: "window" });
    if (sourceFirst) selectVideo();
    selected(false, null);
    if (!sourceFirst) selectVideo();
    const child = f.children.at(-1),
      configuration = child.messages.at(-1);
    assert.equal(configuration.forcePicker, false);
    assert.equal(configuration.autoDetect, true);
    assert.equal(configuration.hint, 300);
    f.ready(old, 2800);
    assert.equal(f.calls.at(-1).soundsharePid, 0);
    f.ready(child, 2600, configuration.generation);
    assert.equal(f.calls.at(-1).soundsharePid, 2600);
    assert.deepEqual(idle.calls, [options]);
    assert.equal(idle.children.length, 0, "ordinary voice connections ignore global picker events");
    cancelled(false);
    f.ready(child, 2900, configuration.generation);
    assert.equal(f.calls.at(-1).soundsharePid, 0);
    f.adapter.destroy();
    idle.adapter.destroy();
  }
});

test("repeated source setters can recover a failed helper and rejected null options stay silent", (t) => {
  const f = fixture(t, undefined, () => true);
  const options = { sourceId: "window-1", type: "window" };
  f.adapter.setDesktopSourceWithOptions(options);
  f.adapter.setTransportOptions({ soundsharePid: 200 });
  const first = f.children[0];
  f.ready(first, 2500);
  first.emit("exit", 1);
  f.adapter.setDesktopSourceWithOptions(options);
  assert.equal(f.children.length, 2);
  assert.equal(f.children[1].messages.at(-1).forcePicker, false);
  f.ready(f.children[1], 2600);
  f.native.setDesktopSourceWithOptions = () => {
    throw Error("invalid source options");
  };
  assert.throws(() => f.adapter.setDesktopSourceWithOptions(null), /invalid source options/);
  assert.equal(f.calls.at(-1).soundsharePid, 0);
  assert.equal(f.children[1].closed, true);
});
