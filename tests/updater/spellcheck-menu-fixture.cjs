module.exports =
  'mainWebContents.setWindowOpenHandler(details=>{external(details.url);return{action:"deny"}});' +
  'popoutWindow.webContents.setWindowOpenHandler(({url})=>((0,securityUtils_1.saferShellOpenExternal)(url),{action:"deny"}))';
