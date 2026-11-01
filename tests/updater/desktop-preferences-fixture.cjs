// The native integration sites retained from the pinned desktop-core bundle.
// Other transform tests compose this with their own independent fixture sites.
module.exports = [
  "new electron_1.BrowserWindow(mainWindowOptions)",
  'return{action:"allow",overrideBrowserWindowOptions:windowOptions}',
  "exports.setupPopout=function(popoutWindow,key,options,webappEndpoint,openerWebContents){",
  "win.setMinimumSize(width,height)",
  "Math.max(MIN_WIDTH,bounds.width)",
  "Math.max(MIN_HEIGHT,bounds.height)",
  "currentWindow.flashFrame(!currentWindow.isFocused()&&flag)",
  "electron_1.default.app.setBadgeCount(count)",
  "electron_1.default.app.dock.setBadge(badge)",
  "function setAppBadge(count,force){",
  "}(count);(force||lastIndex!==index)",
  "function createTray(){if(null!=atomTray)return;",
  ",(IS_WINDOWS||IS_LINUX)&&createTray()};let electron_1=",
  'settings?.get("MINIMIZE_TO_TRAY",!0)',
  "function setWindowVisible(isVisible,andUnminimize,inactive=!1){",
].join("\n");
