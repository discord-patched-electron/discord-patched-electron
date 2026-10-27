const path = require("node:path");

module.exports = function install(app, root, desktopName) {
  // Mod loaders may supply their own package.json without desktopName. Set the
  // identity before ready for the updater, client and native Electron dialogs.
  if (desktopName) app.setDesktopName(desktopName);
  const icon = path.join(root, "discord.png");
  // Cover the splash, settings, main window and any later popouts on X11 too.
  app.on("browser-window-created", (_event, window) => window.setIcon(icon));
};
