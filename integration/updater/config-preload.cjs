// Preserve the pinned settings page's narrow API and add the launch editor.
const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("Native", {
  restart: () => ipcRenderer.send("cr"),
  set: (config) => ipcRenderer.send("cs", config),
  get: () => ipcRenderer.sendSync("cg"),
  open: () => ipcRenderer.send("of"),
  onTab: (callback) => ipcRenderer.on("dpe-config-tab", (_event, tab) => callback(tab)),
});
contextBridge.exposeInMainWorld("LaunchSettings", {
  load: () => ipcRenderer.invoke("dpe-launch-load"),
  preview: (config) => ipcRenderer.invoke("dpe-launch-preview", config),
  save: (config, fileRevision) => ipcRenderer.invoke("dpe-launch-save", config, fileRevision),
  restart: () => ipcRenderer.invoke("dpe-launch-restart"),
});
