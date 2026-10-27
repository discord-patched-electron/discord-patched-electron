const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("LaunchSettings", {
  load: () => ipcRenderer.invoke("dpe-launch-load"),
  preview: (config) => ipcRenderer.invoke("dpe-launch-preview", config),
  save: (config, fileRevision) => ipcRenderer.invoke("dpe-launch-save", config, fileRevision),
  restart: () => ipcRenderer.invoke("dpe-launch-restart"),
});
