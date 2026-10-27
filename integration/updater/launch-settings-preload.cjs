const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("LaunchSettings", {
  load: () => ipcRenderer.invoke("dpe-launch-load"),
  preview: (config) => ipcRenderer.invoke("dpe-launch-preview", config),
  save: (config) => ipcRenderer.invoke("dpe-launch-save", config),
  restart: () => ipcRenderer.invoke("dpe-launch-restart"),
});
