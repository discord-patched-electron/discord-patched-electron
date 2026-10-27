// Confirm a trial only when its Discord window loads, not when Electron starts.
const fs = require("node:fs");

module.exports = function confirmApplication(app, filename, channel, write = fs.writeFileSync) {
  if (!filename) return;
  const hostname = { stable: "discord.com", canary: "canary.discord.com", ptb: "ptb.discord.com" }[
    channel
  ];
  let confirmed = false;
  app.on("browser-window-created", (_event, window) => {
    window.webContents.on("did-finish-load", () => {
      if (confirmed) return;
      try {
        const url = new URL(window.webContents.getURL());
        if (url.protocol !== "https:" || url.hostname !== hostname) return;
        write(filename, "ready\n", { mode: 0o600 });
        confirmed = true;
      } catch (error) {
        console.error("Could not confirm the application startup:", error.message);
      }
    });
  });
};
