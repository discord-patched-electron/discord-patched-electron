// Test the same cross-window contextBridge path as the frame-local PiP API.
const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("DPEPiP", {
  state: () => ipcRenderer.invoke("dpe-test-pip-return-corner"),
});
