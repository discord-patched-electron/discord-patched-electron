const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("Splash", {
  onState: (callback) => ipcRenderer.on("state", (_event, state) => callback(state)),
  quit: () => ipcRenderer.send("dpe-splash-action", "close"),
  retry: () => ipcRenderer.send("dpe-splash-action", "retry"),
  pause: () => ipcRenderer.send("dpe-splash-action", "pause"),
});
