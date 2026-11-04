"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");

function load(directory, helper, onEvent) {
  const metadata = JSON.parse(
    fs.readFileSync(path.join(directory, "capture-picker-scope.json"), "utf8"),
  );
  const filename = path.join(directory, "discord_voice.node");
  const offsets = [metadata.show, metadata.new, metadata.destroy].flatMap((entry) => [
    entry?.slot,
    entry?.target,
  ]);
  offsets.push(metadata.selection?.mutex, metadata.selection?.config, metadata.selection?.present);
  offsets.push(metadata.update?.slot, metadata.update?.target);
  offsets.push(metadata.delivery?.clone, metadata.delivery?.release, metadata.delivery?.destroy);
  if (
    typeof onEvent !== "function" ||
    metadata.schema !== 1 ||
    metadata.abi !== 3 ||
    metadata.supported !== true ||
    offsets.some((value) => !Number.isSafeInteger(value) || value <= 0 || value > 0xffffffff) ||
    createHash("sha256").update(fs.readFileSync(filename)).digest("hex") !== metadata.voice_sha256
  )
    throw new Error("The native module does not match the reviewed external capture bridge.");
  const addon = { exports: {} };
  process.dlopen(addon, helper);
  const api = addon.exports;
  for (const name of ["installCapturePicker", "finishCapturePicker", "cancelCapturePicker"])
    if (typeof api[name] !== "function")
      throw new Error("External capture support is unavailable in this helper.");
  api.installCapturePicker(filename, ...offsets, (encoded) => {
    let event;
    try {
      event = JSON.parse(encoded);
    } catch {
      return;
    }
    if (
      ["request", "started", "stopped", "cancelled"].includes(event?.event) &&
      Number.isSafeInteger(event.requestId) &&
      event.requestId > 0 &&
      Number.isInteger(event.sourceType)
    )
      onEvent(event);
  });
  // Older helpers/scopes retain the existing timer path. Unsupported optional
  // call-site hooks must never make an otherwise valid capture picker fail.
  let eventDelivery = false;
  if (metadata.wake?.abi === 1 && typeof api.installCaptureWake === "function") {
    const wakeOffsets = ["read", "gpu", "cpu", "receive", "overdue"].flatMap((name) => [
      metadata.wake[name]?.call,
      metadata.wake[name]?.target,
    ]);
    if (
      wakeOffsets.every((value) => Number.isSafeInteger(value) && value > 0 && value <= 0xffffffff)
    ) {
      try {
        eventDelivery = api.installCaptureWake(filename, ...wakeOffsets) === true;
      } catch {
        // Keep the picker usable when optional delivery hooks are unavailable.
      }
    }
  }
  function requestId(value) {
    if (!Number.isInteger(value) || value <= 0 || value > 0xffffffff)
      throw new Error("Invalid capture request.");
    return value;
  }
  return {
    eventDelivery,
    finish(id, action, socketPath = "", nodeId = 0) {
      requestId(id);
      if (!["native", "selected", "cancel", "error"].includes(action))
        throw new Error("Invalid capture selection action.");
      if (
        action === "selected" &&
        (typeof socketPath !== "string" ||
          !path.isAbsolute(socketPath) ||
          socketPath.includes("\0") ||
          Buffer.byteLength(socketPath) >= 108 ||
          !Number.isInteger(nodeId) ||
          nodeId < 0 ||
          nodeId >= 0xffffffff)
      )
        throw new Error("Invalid external capture endpoint.");
      return api.finishCapturePicker(id, action, action === "selected" ? socketPath : "", nodeId);
    },
    cancel: (id) => api.cancelCapturePicker(requestId(id)),
  };
}

module.exports = { load };
