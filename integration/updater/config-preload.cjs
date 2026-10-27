// Preserve the pinned settings page's narrow API and add the launch editor.
const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("Native", {
  restart: () => ipcRenderer.send("cr"),
  set: (config) => ipcRenderer.send("cs", config),
  get: () => ipcRenderer.sendSync("cg"),
  open: () => ipcRenderer.send("of"),
  flags: () => ipcRenderer.send("dpe-launch-settings-open"),
});
