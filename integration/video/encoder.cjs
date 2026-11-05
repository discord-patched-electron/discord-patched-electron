"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");

function load(directory, helper) {
  const metadata = JSON.parse(fs.readFileSync(path.join(directory, "encoder-scope.json"), "utf8"));
  const filename = path.join(directory, "discord_voice.node");
  const offsets = [
    metadata.create?.slot,
    metadata.create?.target,
    metadata.create?.lazy,
    metadata.update?.slot,
    metadata.update?.target,
    metadata.update?.lazy,
    metadata.destroy?.slot,
    metadata.destroy?.target,
    metadata.destroy?.lazy,
    metadata.vendor?.target,
  ];
  const sequence = [metadata.av1_sequence?.target, ...(metadata.av1_sequence?.calls ?? [])];
  const quality = [
    metadata.amd_quality?.slot,
    metadata.amd_quality?.target,
    metadata.amd_quality?.query,
  ];
  const qpTrust = [
    metadata.qp_trust?.slot,
    metadata.qp_trust?.target,
    metadata.qp_trust?.bandwidth_call,
    metadata.qp_trust?.config_target,
  ];
  const transitions = [
    metadata.transitions?.quality?.target,
    ...(metadata.transitions?.quality?.calls ?? []),
    metadata.transitions?.bandwidth?.target,
    ...(metadata.transitions?.bandwidth?.calls ?? []),
    metadata.transitions?.started,
    metadata.transitions?.codec,
  ];
  const allocation = [
    metadata.allocation?.rate_call,
    metadata.allocation?.rate_target,
    metadata.allocation?.budget_call,
    metadata.allocation?.budget_target,
    ...(metadata.allocation?.destructors ?? []).flatMap((entry) => [entry.slot, entry.target]),
  ];
  const streamLimit = [
    metadata.stream_limit?.call,
    metadata.stream_limit?.target,
    metadata.stream_limit?.info_destructor,
  ];
  if (
    streamLimit.some((value) => !Number.isSafeInteger(value) || value <= 0 || value > 0xffffffff) ||
    allocation.length !== 8 ||
    allocation.some((value) => !Number.isSafeInteger(value) || value <= 0 || value > 0xffffffff) ||
    transitions.length !== 10 ||
    transitions.some((value) => !Number.isSafeInteger(value) || value <= 0 || value > 0xffffffff) ||
    metadata.schema !== 1 ||
    metadata.abi !== 1 ||
    metadata.supported !== true ||
    sequence.length !== 3 ||
    sequence.some((value) => !Number.isSafeInteger(value) || value <= 0 || value > 0xffffffff) ||
    quality.some((value) => !Number.isSafeInteger(value) || value <= 0 || value > 0xffffffff) ||
    qpTrust.some((value) => !Number.isSafeInteger(value) || value <= 0 || value > 0xffffffff) ||
    offsets.some((value) => !Number.isSafeInteger(value) || value <= 0 || value > 0xffffffff) ||
    createHash("sha256").update(fs.readFileSync(filename)).digest("hex") !== metadata.voice_sha256
  )
    throw new Error("The native encoder does not match the reviewed compatibility guard.");
  const addon = { exports: {} };
  process.dlopen(addon, helper);
  if (
    typeof addon.exports.installEncoderGuard !== "function" ||
    typeof addon.exports.installAv1Levels !== "function" ||
    typeof addon.exports.installEncoderQuality !== "function" ||
    typeof addon.exports.installEncoderQpTrust !== "function" ||
    typeof addon.exports.installScalerTransitions !== "function" ||
    typeof addon.exports.installEncoderAllocation !== "function" ||
    typeof addon.exports.installStreamBitrateEnvelope !== "function"
  )
    throw new Error("Encoder compatibility support is unavailable in this helper.");
  addon.exports.installEncoderGuard(filename, ...offsets);
  addon.exports.installAv1Levels(filename, ...sequence);
  addon.exports.installStreamBitrateEnvelope(filename, ...streamLimit);
  addon.exports.installEncoderAllocation(filename, ...allocation);
  addon.exports.installScalerTransitions(filename, ...transitions);
  addon.exports.installEncoderQpTrust(filename, ...qpTrust);
  addon.exports.installEncoderQuality(filename, ...quality);
  if (metadata.hevc) {
    const { abi, offsets } = metadata.hevc;
    if (
      abi !== 1 ||
      !Array.isArray(offsets) ||
      offsets.length !== 19 ||
      offsets.some((value) => !Number.isSafeInteger(value) || value < 0 || value > 0xffffffff) ||
      typeof addon.exports.installHevcEncoder !== "function"
    )
      throw new Error("HEVC encoder metadata does not match the reviewed interface.");
    addon.exports.installHevcEncoder(filename, ...offsets);
  }
  if (metadata.software_av1_crop) {
    const { abi, offsets } = metadata.software_av1_crop;
    if (
      abi !== 1 ||
      !Array.isArray(offsets) ||
      offsets.length !== 10 ||
      offsets.some((value) => !Number.isSafeInteger(value) || value <= 0 || value > 0xffffffff) ||
      typeof addon.exports.installSoftwareAv1Crop !== "function" ||
      typeof addon.exports.softwareAv1CropStats !== "function"
    )
      throw new Error("Software AV1 crop metadata does not match the reviewed interface.");
    addon.exports.installSoftwareAv1Crop(filename, ...offsets);
  }
  return {
    snapshot: () => ({
      ...JSON.parse(addon.exports.encoderGuardStats()),
      ...(metadata.software_av1_crop
        ? { softwareAv1Crop: JSON.parse(addon.exports.softwareAv1CropStats()) }
        : {}),
    }),
    decoderHardware: (name) =>
      typeof metadata.decoder_hardware?.[name] === "boolean"
        ? metadata.decoder_hardware[name]
        : /^(?:ffmpeg|openh264|libaom)$/i.test(name || "")
          ? false
          : null,
  };
}

let initialized = false,
  adapter;
function install(directory) {
  if (initialized) return;
  initialized = true;
  if (process.platform !== "linux") return;
  try {
    adapter = load(directory, path.join(process.env.DPE_APP_ROOT, "libdpe-camera-probe.so"));
  } catch (error) {
    console.error("DPE encoder compatibility:", error.message);
  }
}

module.exports = {
  load,
  install,
  snapshot: () => adapter?.snapshot() ?? null,
  decoderHardware: (name) => adapter?.decoderHardware(name) ?? null,
};
