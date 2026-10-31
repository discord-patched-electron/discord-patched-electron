const { test } = require("node:test");
const assert = require("node:assert/strict");
const { install } = require("../../integration/audio/voice-devices.cjs");
test("private devices are hidden from initial lists and hotplug without changing real devices", () => {
  const real = { guid: "alsa_output.pci", name: "Headphones" };
  const other = { guid: "other_virtual_sink", name: "Virtual audio" };
  const internal = { guid: "dpe-audio-" + "a".repeat(32) + "-native", name: "DPE-Private-Audio" };
  const monitor = { guid: internal.guid + ".monitor", name: "Monitor" };
  let change;
  const voice = {
    getOutputDevices: (cb) => cb([real, internal, other]),
    getInputDevices: (cb) => cb([real, monitor]),
    setDeviceChangeCallback: (cb) => {
      change = cb;
    },
  };
  install(voice);
  install(voice);
  let outputCalls = 0,
    inputCalls = 0,
    changeCalls = 0;
  voice.getOutputDevices((list) => {
    outputCalls++;
    assert.deepEqual(list, [real, other]);
  });
  voice.getInputDevices((list) => {
    inputCalls++;
    assert.deepEqual(list, [real]);
  });
  assert.equal(outputCalls, 1);
  assert.equal(inputCalls, 1);
  const camera = { guid: "camera", name: "Camera" };
  voice.setDeviceChangeCallback((inputs, outputs, cameras) => {
    changeCalls++;
    assert.deepEqual(inputs, [real]);
    assert.deepEqual(outputs, [other]);
    assert.deepEqual(cameras, [camera]);
  });
  change([real, monitor], [internal, other], [camera]);
  assert.equal(changeCalls, 1);
  voice.setDeviceChangeCallback(null);
  assert.equal(change, null);
});

test("enumeration preserves receivers, return values, errors and extra callback arguments", () => {
  let called = 0;
  const error = new Error("native enumeration failed");
  const voice = {
    getInputDevices(callback, argument) {
      assert.equal(this, voice);
      assert.equal(argument, "request");
      callback(null, "native-status");
      return 17;
    },
    getOutputDevices() {
      throw error;
    },
  };
  install(voice);
  assert.equal(
    voice.getInputDevices((devices, status) => {
      called++;
      assert.equal(devices, null);
      assert.equal(status, "native-status");
    }, "request"),
    17,
  );
  assert.equal(called, 1);
  assert.throws(
    () => voice.getOutputDevices(() => {}),
    (value) => value === error,
  );
});

test("private sink churn does not notify Discord or reset call devices", () => {
  let change;
  const voice = {
    setDeviceChangeCallback(cb) {
      change = cb;
    },
  };
  install(voice);
  const output = { guid: "headphones", name: "Headphones", index: 1 };
  const input = { guid: "mic", name: "Microphone", index: 2 };
  const camera = { guid: "camera", name: "Camera" };
  const privateSink = { guid: "dpe-audio-" + "a".repeat(32) + "-mix" };
  const events = [];
  voice.setDeviceChangeCallback((...args) => events.push(args));
  change([input], [output], [camera]);
  change([input, privateSink], [output, privateSink], [camera]);
  change([input], [output], [camera]);
  assert.equal(events.length, 1);
  output.index = 3;
  change([input], [output], [camera]);
  assert.equal(events.length, 2, "a native index change must reach Discord");
  change([input], [output], []);
  assert.equal(events.length, 3, "camera hotplug is preserved");
  change([], [output], []);
  assert.equal(events.length, 4, "microphone removal is preserved");
  voice.setDeviceChangeCallback((...args) => events.push(args));
  change([], [output], []);
  assert.equal(events.length, 5, "a replacement subscriber receives its initial state");
});
