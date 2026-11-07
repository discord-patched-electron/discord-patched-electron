"use strict";
// Test-only RPC: commands and aggregate counters cross processes, never pixels.
const { ipcRenderer } = require("electron");
async function create(endpoint) {
  await ipcRenderer.invoke("receiver-call", "initialize", [endpoint]);
  let failure;
  const callbacks = new Map();
  ipcRenderer.on("receiver-event", (_, name, value) => callbacks.get(name)?.(value));
  const invoke = async (method, args) => {
    if (failure) throw failure;
    try {
      return await ipcRenderer.invoke("receiver-call", method, args);
    } catch (error) {
      failure = error;
      throw error;
    }
  };
  const notify = (method, args) => {
    void invoke(method, args).catch(() => {});
  };
  return {
    setTransportOptions: (value) => notify("setTransportOptions", [value]),
    mergeUsers: (value) => notify("mergeUsers", [value]),
    setDisableLocalVideo: (...args) => notify("setDisableLocalVideo", args),
    setSelfMute: (value) => notify("setSelfMute", [value]),
    executeSecureFramesTransition: (value) => notify("executeSecureFramesTransition", [value]),
    prepareSecureFramesTransition(a, b, done) {
      void invoke("prepareSecureFramesTransition", [a, b]).then(done, () => done());
    },
    getFilteredStats(mask, done) {
      void invoke("getFilteredStats", [mask]).then(done, (error) =>
        done(JSON.stringify({ __fixtureError: String(error) })),
      );
    },
    setOnVideoDecoderFallbackCallback: (callback) => callbacks.set("decoder-fallback", callback),
    setVideoCodecErrorCallback: (callback) => callbacks.set("codec-error", callback),
    setPhase: (value) => notify("phase", [value]),
    progress: () => invoke("progress", []),
    async output() {
      return invoke("output", []);
    },
    destroy: () => invoke("destroy", []),
  };
}
module.exports = { create };
