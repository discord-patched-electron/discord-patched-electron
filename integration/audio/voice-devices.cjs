"use strict";
const installed = new WeakSet();
// Only our UUID-named internal sinks/monitors. Real hardware notifications and
// other virtual audio devices remain visible; native routing still sees ours.
function internal(device) {
  return [device?.guid, device?.name].some(
    (value) =>
      typeof value === "string" &&
      /^dpe-audio-[a-f0-9]{32}-(?:mix|native)(?:\.monitor)?$/.test(value),
  );
}
function filter(devices) {
  return Array.isArray(devices) ? devices.filter((d) => !internal(d)) : devices;
}
function install(voice) {
  if (installed.has(voice)) return;
  installed.add(voice);
  for (const name of ["getInputDevices", "getOutputDevices"]) {
    if (typeof voice[name] !== "function") continue;
    const original = voice[name].bind(voice);
    voice[name] = (callback, ...rest) =>
      original(
        typeof callback === "function"
          ? (devices, ...args) => callback(filter(devices), ...args)
          : callback,
        ...rest,
      );
  }
  if (typeof voice.setDeviceChangeCallback === "function") {
    const original = voice.setDeviceChangeCallback.bind(voice);
    voice.setDeviceChangeCallback = (callback) =>
      original(
        typeof callback === "function"
          ? (inputs, outputs, ...rest) => callback(filter(inputs), filter(outputs), ...rest)
          : callback,
      );
  }
}
module.exports = { install, internal, filter };
