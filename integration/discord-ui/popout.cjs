"use strict";
// Use Discord's own action so Show Members remains usable and translations do
// not affect discovery. Only an explicit expansion of a stream collapses it.
module.exports = function popoutPreferences(request, ui) {
  const key = Symbol.for("dpe.popoutPreferences");
  const existing = globalThis[key];
  if (existing) {
    existing.hideMembers = request.hideMembers === true;
    return;
  }
  const state = { hideMembers: request.hideMembers === true };
  globalThis[key] = state;
  const stores = new Set(),
    actions = new WeakSet();
  ui.watch(
    "popout-members",
    (source) =>
      source.includes("CHANNEL_RTC_SELECT_PARTICIPANT") || source.includes("ChannelRTCStore"),
    (exports) => {
      for (const value of [exports, ...Object.values(exports || {})]) {
        if (!value || typeof value !== "object") continue;
        if (
          [
            "getStreamParticipants",
            "getSelectedParticipant",
            "getSelectedParticipantId",
            "getParticipantsOpen",
          ].every((name) => typeof value[name] === "function")
        )
          stores.add(value);
        if (
          typeof value.selectParticipant !== "function" ||
          typeof value.toggleParticipants !== "function" ||
          actions.has(value)
        )
          continue;
        const original = value.selectParticipant;
        const sig = ui.signature(original);
        if (!sig || !ui.has(sig, "CHANNEL_RTC_SELECT_PARTICIPANT", "channelId")) continue;
        const wrapped = function (channelId, participantId, ...rest) {
          const result = Reflect.apply(original, this, [channelId, participantId, ...rest]);
          if (state.hideMembers && participantId != null)
            queueMicrotask(() => {
              if (!state.hideMembers || stores.size !== 1) return;
              try {
                const store = [...stores][0];
                const selected = store.getSelectedParticipant(channelId);
                // The stream collection avoids hardcoding Discord's numeric type enum.
                if (
                  selected &&
                  store.getSelectedParticipantId(channelId) === participantId &&
                  store.getStreamParticipants(channelId).includes(selected) &&
                  store.getParticipantsOpen(channelId)
                )
                  value.toggleParticipants(channelId, false);
              } catch {
                /* A changed upstream store retains its native behavior. */
              }
            });
          return result;
        };
        wrapped.toString = () => original.toString();
        try {
          value.selectParticipant = wrapped;
          if (value.selectParticipant === wrapped) actions.add(value);
        } catch {
          /* Unsupported frozen exports retain native behavior. */
        }
      }
    },
  );
};
