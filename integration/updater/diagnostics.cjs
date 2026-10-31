const pages = {
  gpu: { title: "GPU", description: "Graphics acceleration, drivers and video codec support." },
  "media-internals": {
    title: "Media",
    description: "Playback pipelines, decoders and media errors.",
  },
  "webrtc-internals": {
    title: "WebRTC",
    description: "Connection and capture statistics. Open before reproducing a call issue.",
  },
  histograms: { title: "Histograms", description: "Chromium performance and internal counters." },
  "process-internals": {
    title: "Processes",
    description: "Renderer processes, frames and site isolation.",
  },
  "network-errors": {
    title: "Network errors",
    description: "Reference for Chromium network error codes.",
  },
};
const windows = new Map();
async function open(name) {
  if (typeof name !== "string" || !Object.hasOwn(pages, name))
    throw new Error("Unknown diagnostic page");
  const existing = windows.get(name);
  if (existing && !existing.isDestroyed()) {
    existing.show();
    existing.focus();
    return;
  }
  const { BrowserWindow } = require("electron");
  const window = new BrowserWindow({
    width: 1000,
    height: 760,
    title: `DPE — ${pages[name].title}`,
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false },
  });
  windows.set(name, window);
  window.once("closed", () => windows.delete(name));
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event, url) => {
    if (!url.startsWith("chrome://")) event.preventDefault();
  });
  try {
    await window.loadURL(`chrome://${name}/`);
  } catch {
    window.destroy();
    throw new Error(`${pages[name].title} is unavailable in this Electron runtime.`);
  }
}
module.exports = { pages, open };
