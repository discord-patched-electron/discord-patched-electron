const { test } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const {
  wrap,
  installVoice,
} = require("../../integration/audio/wayland-audio.cjs");
function fixture() {
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
  const adapter = wrap(native, { spawnHelper, warn() {} });
  const ready = (child, pid, generation = child.messages.at(-1).generation) =>
    child.stdout.emit(
      "data",
      Buffer.from(JSON.stringify({ generation, pid, state: "ready" }) + "\n"),
    );
  return { adapter, calls, children, ready, end: () => ended() };
}
test("Wayland never forwards the system sentinel before the selected mix is ready", () => {
  const f = fixture();
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
  assert.equal(
    f.calls.find((x) => x.encryptionKey)?.encryptionKey,
    "untouched",
  );
  f.ready(f.children[0], 2500);
  assert.equal(f.calls.at(-1).soundsharePid, 2500);
  f.adapter.setSelfMute(true);
  assert.deepEqual(f.calls.at(-1), { mute: true });
  f.adapter.setTransportOptions({ soundsharePid: 0 });
  assert.equal(f.calls.at(-1).soundsharePid, 0);
  assert.equal(f.children[0].closed, true);
  f.adapter.destroy();
});
test("late helper responses cannot restore audio after switching or ending a source", () => {
  const f = fixture();
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
test("native picker callbacks stop capture while choosing and after cancellation", () => {
  const previous = process.env.DPE_APP_AUDIO;
  const previousLatency = process.env.PULSE_LATENCY_MSEC;
  delete process.env.PULSE_LATENCY_MSEC;
  process.env.DPE_APP_AUDIO = "1";
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
    const f = fixture();
    f.adapter.setDesktopSource("screen:0:0");
    f.adapter.setTransportOptions({ soundsharePid: 1 });
    f.ready(f.children[0], 2500);
    assert.equal(voice.presentNativeScreenSharePicker("window"), 17);
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
  } finally {
    if (previousLatency === undefined) delete process.env.PULSE_LATENCY_MSEC;
    else process.env.PULSE_LATENCY_MSEC = previousLatency;
    if (previous === undefined) delete process.env.DPE_APP_AUDIO;
    else process.env.DPE_APP_AUDIO = previous;
  }
});
