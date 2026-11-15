// Narrow frame-local bridge. No arbitrary IPC, window IDs or settings writes.
const { contextBridge, ipcRenderer } = require("electron");
if (
  process.isMainFrame &&
  location.protocol === "https:" &&
  ["discord.com", "canary.discord.com", "ptb.discord.com"].includes(location.hostname) &&
  location.pathname === "/popout"
) {
  contextBridge.exposeInMainWorld("DPEPiP", {
    openSettings: () => ipcRenderer.invoke("dpe-pip-corner", "settings"),
    state: () => ipcRenderer.invoke("dpe-pip-corner"),
    choose: (corner) => ipcRenderer.invoke("dpe-pip-corner", corner),
    monitor: (direction) =>
      ["left", "right"].includes(direction)
        ? ipcRenderer.invoke("dpe-pip-corner", `monitor-${direction}`)
        : Promise.resolve({ available: false }),
  });
}
