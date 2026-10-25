const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("Splash", {
  onState: (callback) => ipcRenderer.on("state", (_event, state) => callback(state)),
  copyDetails: () => ipcRenderer.invoke("dpe-splash-copy-details"),
  retry: () => ipcRenderer.send("dpe-splash-action", "retry"),
  pause: () => ipcRenderer.send("dpe-splash-action", "pause"),
});
