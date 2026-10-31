"use strict";
// Only updates the user's stream-audio preference. Does not start/stop video,
// enumerate devices, open a picker, or change voice-channel audio.
module.exports = function streamAudio(request, ui) {
  for (const runtime of ui.runtimes()) {
    const update = ui
      .inspect(runtime)
      .choose("updateStreamSettings", (signature) =>
        ui.has(signature, "STREAM_UPDATE_SETTINGS", "dispatch", "soundshareEnabled", "noTrack"),
      );
    if (!update) continue;
    if (request?.action === "disable") update({ soundshareEnabled: false, noTrack: true });
    return true;
  }
  return false;
};
