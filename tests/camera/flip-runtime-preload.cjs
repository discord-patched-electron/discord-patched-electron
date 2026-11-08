const { ipcRenderer } = require("electron");
const path = require("node:path");
try {
  globalThis.window.DiscordNative = {
    isRenderer: true,
    settings: { getSync: (_key, fallback) => fallback },
    features: { declareSupported() {} },
    processUtils: {
      getMainArgvSync: () => ["test", "--use-fake-audio-capture", "--use-fake-video-capture"],
    },
    fileManager: {
      getModuleDataPathSync: () => process.env.DPE_PROFILE_DIR,
      getAndCreateLogDirectorySync: () => process.env.DPE_PROFILE_DIR,
    },
  };
  require(path.join(process.env.DPE_FLIP_TEST_VOICE, "index.js"));
} catch (error) {
  ipcRenderer.send("dpe-flip-test-error", error.stack);
}
