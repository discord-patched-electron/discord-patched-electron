const { ipcRenderer } = require("electron");
const path = require("node:path");
let state = { active: false };
let delay = 0;
let methods = ["auto", "portal"];
ipcRenderer.on("dpe-fixture-methods", (_event, next) => {
  methods = next;
});
ipcRenderer.on("dpe-fixture-status", (_event, next, wait = 0) => {
  state = next;
  delay = wait;
});
require(path.join(process.env.DPE_APP_ROOT, "integration/video/settings.cjs")).install({
  snapshot: async () => {
    if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
    return state;
  },
  methods: () => methods,
});
