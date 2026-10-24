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
  voice.getOutputDevices((list) => assert.deepEqual(list, [real, other]));
  voice.getInputDevices((list) => assert.deepEqual(list, [real]));
  const camera = { guid: "camera", name: "Camera" };
  voice.setDeviceChangeCallback((inputs, outputs, cameras) => {
    assert.deepEqual(inputs, [real]);
    assert.deepEqual(outputs, [other]);
    assert.deepEqual(cameras, [camera]);
  });
  change([real, monitor], [internal, other], [camera]);
  voice.setDeviceChangeCallback(null);
  assert.equal(change, null);
});
