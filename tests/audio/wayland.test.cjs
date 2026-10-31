const { test } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { wrap, installVoice } = require("../../integration/audio/wayland-audio.cjs");
function fixture(t, pickerUI) {
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
  const adapter = wrap(native, { spawnHelper, warn() {}, pickerUI });
  t.after(() => adapter.destroy());
  const ready = (child, pid, generation = child.messages.at(-1).generation) =>
    child.stdout.emit(
      "data",
      Buffer.from(JSON.stringify({ generation, pid, state: "ready" }) + "\n"),
    );
  return { adapter, native, calls, children, ready, end: () => ended() };
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
