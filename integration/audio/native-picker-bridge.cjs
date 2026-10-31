"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");
const render = require("./native-picker-renderer.cjs");
const discover = require("./native-picker-discovery.cjs");
function show(options, respond, { execute, fallback, resolveIcon }) {
  const key = "dpe-audio-" + randomUUID(),
    css = fs.readFileSync(path.join(__dirname, "native-picker.css"), "utf8");
  let state = "native",
    closeFallback,
    pollTimer,
    retryTimer,
    deadline,
    attempts = 0,
    next = 0,
    nativeCancelled = false;
  const run = (request) =>
    Promise.resolve().then(() =>
      execute(
        `(${render.toString()})(${JSON.stringify({ ...request, key })},${discover.toString()})`,
      ),
    );
  function clearTimers() {
    clearTimeout(pollTimer);
    clearTimeout(retryTimer);
    clearTimeout(deadline);
  }
  function cancelNative() {
    if (nativeCancelled) return;
    nativeCancelled = true;
    void run({ operation: "close" }).catch(() => {});
  }
  function useFallback() {
    if (state !== "native") return;
    state = "fallback";
    clearTimers();
    // Cancel before opening the DOM picker; a delayed native open must stay dead.
    cancelNative();
    try {
      closeFallback = fallback();
    } catch {
      state = "done";
      respond("fallback");
    }
  }
  function open() {
    if (state !== "native") return;
    attempts++;
    run({ operation: "open", options, css }).then((result) => {
      if (state !== "native") return;
      if (
        result?.unavailable &&
        ["runtime-unavailable", "no-compatible-runtime", "missing-components"].includes(
          result.reason,
        ) &&
        attempts < 3
      ) {
        retryTimer = setTimeout(open, 200);
        return;
      }
      const choice = result?.selection;
      if (choice === "none" || choice === "refresh" || options.some((o) => o.id === choice)) {
        state = "done";
        clearTimers();
        respond(choice);
      } else useFallback();
    }, useFallback);
  }
  async function icons() {
    while (state === "native" && next < options.length) {
      const option = options[next++];
      if (option.id === "system") continue;
      try {
        const data = await resolveIcon(option.iconNames);
        if (state === "native" && data) await run({ operation: "icon", id: option.id, data });
      } catch {
        /* One bad image must not stop the remaining sources. */
      }
    }
  }
  function checkMounted() {
    if (state !== "native") return;
    run({ operation: "status" }).then((result) => {
      if (state !== "native") return;
      if (result?.mounted) {
        clearTimeout(deadline);
        for (let worker = 0; worker < 4; worker++) void icons();
      } else pollTimer = setTimeout(checkMounted, 200);
    }, useFallback);
  }
  // Independent of the page promise: never confuse a stalled execution with a
  // user taking their time after a validated modal has mounted.
  deadline = setTimeout(useFallback, 4000);
  pollTimer = setTimeout(checkMounted, 200);
  open();
  return () => {
    if (state === "closed") return;
    state = "closed";
    clearTimers();
    try {
      closeFallback?.();
    } finally {
      cancelNative();
    }
  };
}
module.exports = { show };
