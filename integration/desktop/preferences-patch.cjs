// Validated, narrowly scoped calls at Discord's existing native integration
// points. The client archive and renderer application code stay untouched.
function patch(source, adapter, replace) {
  const load = `require(${JSON.stringify(adapter)})`;
  // Canary 1.0.2357 (the locked baseline) predates the opener argument added
  // in 1.0.2422. Both retain the same options object and setup lifecycle.
  const popoutSignatures = [
    "exports.setupPopout=function(popoutWindow,key,options,webappEndpoint,openerWebContents){",
    "exports.setupPopout=function(popoutWindow,key,options,webappEndpoint){",
  ];
  const matches = popoutSignatures.filter((signature) => source.includes(signature));
  if (matches.length > 1) throw new Error("Ambiguous Discord popout setup layout");
  const popoutSetup = matches[0] || popoutSignatures[0];
  const changes = [
    [
      "let{url,frameName,features}=details,frameViewResponse=",
      `${load}.tracePopoutRequest(details);let{url,frameName,features}=details,frameViewResponse=`,
    ],
    [
      "existingWindow.setAlwaysOnTop(!0),existingWindow.focus(),existingWindow.setAlwaysOnTop(!1)",
      `${load}.focusPopout(existingWindow,key)`,
    ],
    [
      "new electron_1.BrowserWindow(mainWindowOptions)",
      `${load}.createMainWindow(electron_1.BrowserWindow,mainWindowOptions)`,
    ],
    [
      "overrideBrowserWindowOptions:windowOptions}",
      `overrideBrowserWindowOptions:${load}.popoutOptions(windowOptions,key,parentWindow)}`,
    ],
    [
      "popoutWindows.openOrFocusWindow(url,frameName,features)",
      "popoutWindows.openOrFocusWindow(url,frameName,features,mainWindow)",
    ],
    [
      "exports.openOrFocusWindow=function(windowURL,key,features){",
      "exports.openOrFocusWindow=function(windowURL,key,features,parentWindow){",
    ],
    [popoutSetup, `${popoutSetup}${load}.attachPopout(popoutWindow,options);`],
    ["win.setMinimumSize(width,height)", `${load}.setMinimumSize(win,width,height)`],
    ["Math.max(MIN_WIDTH,bounds.width)", `Math.max(${load}.minimum(MIN_WIDTH),bounds.width)`],
    ["Math.max(MIN_HEIGHT,bounds.height)", `Math.max(${load}.minimum(MIN_HEIGHT),bounds.height)`],
    [
      "currentWindow.flashFrame(!currentWindow.isFocused()&&flag)",
      `${load}.flashFrame(currentWindow,!currentWindow.isFocused()&&flag)`,
    ],
    [
      "electron_1.default.app.setBadgeCount(count)",
      `${load}.setBadgeCount(electron_1.default.app,count)`,
    ],
    [
      "electron_1.default.app.dock.setBadge(badge)",
      `${load}.setDockBadge(electron_1.default.app.dock,badge)`,
    ],
    [
      "function setAppBadge(count,force){",
      `function setAppBadge(count,force){${load}.rememberOverlay(count,()=>setAppBadge(count,!0));`,
    ],
    [
      "}(count);(force||lastIndex!==index)",
      `}(${load}.badgeCount(count));(force||lastIndex!==index)`,
    ],
    [
      "function getTrayStates(){",
      `function getTrayStates(){return ${load}.trayStates(getRawTrayStates())}function getRawTrayStates(){`,
    ],
    [
      "function createTray(){if(null!=atomTray)return;",
      `function createTray(){if(!${load}.trayEnabled()||null!=atomTray)return;`,
    ],
    [
      ",(IS_WINDOWS||IS_LINUX)&&createTray()};let electron_1=",
      `,${load}.registerTray(createTray,()=>{atomTray?.destroy();atomTray=null},()=>{if(atomTray&&!atomTray.isDestroyed())atomTray.setImage(getTrayIcon())}),(IS_WINDOWS||IS_LINUX)&&createTray()};let electron_1=`,
    ],
    [
      'settings?.get("MINIMIZE_TO_TRAY",!0)',
      `${load}.minimizeToTray(settings?.get("MINIMIZE_TO_TRAY",!0))`,
    ],
    [
      "function setWindowVisible(isVisible,andUnminimize,inactive=!1){",
      `function setWindowVisible(isVisible,andUnminimize,inactive=!1){if(!isVisible&&${load}.minimizeWithoutTray(mainWindow)){mainWindowIsVisible=!0;return}`,
    ],
  ];
  for (const [before, after] of changes) source = replace(source, before, after);
  return source;
}
module.exports = { patch };
