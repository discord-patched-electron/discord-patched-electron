// Narrow frame-local bridge. No arbitrary IPC, window IDs or settings writes.
const { contextBridge, ipcRenderer } = require("electron");
if (
  process.isMainFrame &&
  location.protocol === "https:" &&
  ["discord.com", "canary.discord.com", "ptb.discord.com"].includes(location.hostname) &&
  location.pathname === "/popout"
) {
  contextBridge.exposeInMainWorld("DPEPiP", {
    state: () => ipcRenderer.invoke("dpe-pip-corner"),
    choose: (corner) => ipcRenderer.invoke("dpe-pip-corner", corner),
  });
}
