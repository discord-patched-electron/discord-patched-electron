// Preserve the pinned settings page's narrow API and add the launch editor.
const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("Native", {
  getMode: () => ipcRenderer.sendSync("dpe-config-mode"),
  isOnboardingSettings: () => ipcRenderer.sendSync("dpe-config-onboarding"),
  ready: () => ipcRenderer.invoke("dpe-config-ready"),
  setupAction: (action) => ipcRenderer.invoke("dpe-setup-action", action),
  presentation: () => ipcRenderer.invoke("dpe-presentation"),
  onPresentation: (callback) =>
    ipcRenderer.on("dpe-presentation", (_event, value) => callback(value)),
  windowAction: (action) => ipcRenderer.invoke("dpe-window-action", action),
  copyCommand: (text) => ipcRenderer.invoke("dpe-copy-command", text),
  copyRecovery: () => ipcRenderer.invoke("dpe-copy-recovery"),
  copyVersions: () => ipcRenderer.invoke("dpe-copy-versions"),
  restart: () => ipcRenderer.send("cr"),
  set: (config) => ipcRenderer.send("cs", config),
  get: () => ipcRenderer.sendSync("cg"),
  desktopLoad: () => ipcRenderer.invoke("dpe-desktop-load"),
  desktopSave: (patch) => ipcRenderer.invoke("dpe-desktop-save", patch),
  screenshareLoad: () => ipcRenderer.invoke("dpe-screenshare-load"),
  screenshareSave: (patch) => ipcRenderer.invoke("dpe-screenshare-save", patch),
  screenshareStatus: () => ipcRenderer.invoke("dpe-screenshare-status"),
  capturePermissionStatus: () => ipcRenderer.invoke("dpe-capture-permission-status"),
  capturePermissionAllow: () => ipcRenderer.invoke("dpe-capture-permission-allow"),
  capturePermissionRevoke: () => ipcRenderer.invoke("dpe-capture-permission-revoke"),
  captureSelection: () => ipcRenderer.invoke("dpe-capture-selection"),
  captureRefresh: () => ipcRenderer.invoke("dpe-capture-refresh"),
  captureChoose: (id, source, authorize) =>
    ipcRenderer.invoke("dpe-capture-choose", id, source, authorize === true),
  captureCancel: (id) => ipcRenderer.invoke("dpe-capture-cancel", id),
  open: () => ipcRenderer.send("of"),
  spellcheckLoad: (refresh = false) => ipcRenderer.invoke("dpe-spellcheck-load", refresh),
  spellcheckUninstall: (language) => ipcRenderer.invoke("dpe-spellcheck-uninstall", language),
  spellcheckSave: (value) => ipcRenderer.invoke("dpe-spellcheck-save", value),
  trayLoad: () => ipcRenderer.invoke("dpe-tray-load"),
  trayPreset: (preset) => ipcRenderer.invoke("dpe-tray-preset", preset),
  trayStatePreset: (state, preset) => ipcRenderer.invoke("dpe-tray-state-preset", state, preset),
  trayChoose: (state) => ipcRenderer.invoke("dpe-tray-choose", state),
  trayReset: (state) => ipcRenderer.invoke("dpe-tray-reset", state),
  exportLogs: () => ipcRenderer.invoke("dpe-export-logs"),
  openDiagnostic: (name) => ipcRenderer.invoke("dpe-config-diagnostic", name),
  openLink: (name) => ipcRenderer.invoke("dpe-config-open-link", name),
  onTab: (callback) => ipcRenderer.on("dpe-config-tab", (_event, tab) => callback(tab)),
  onSetupReminder: (callback) =>
    ipcRenderer.on("dpe-setup-reminder", (_event, message) => callback(message)),
  onSetupError: (callback) =>
    ipcRenderer.on("dpe-setup-error", (_event, message) => callback(message)),
});
contextBridge.exposeInMainWorld("LaunchSettings", {
  setDirty: (dirty) => ipcRenderer.send("dpe-launch-dirty", dirty),
  setSaving: (saving) => ipcRenderer.send("dpe-launch-saving", saving),
  load: () => ipcRenderer.invoke("dpe-launch-load"),
  preview: (config) => ipcRenderer.invoke("dpe-launch-preview", config),
  save: (config, fileRevision) => ipcRenderer.invoke("dpe-launch-save", config, fileRevision),
  restart: () => ipcRenderer.invoke("dpe-launch-restart"),
});
contextBridge.exposeInMainWorld("ModSettings", {
  load: () => ipcRenderer.invoke("dpe-mod-load"),
  apply: (choice) => ipcRenderer.invoke("dpe-mod-apply", choice),
  complete: () => ipcRenderer.invoke("dpe-mod-complete"),
  onProgress: (callback) => ipcRenderer.on("dpe-mod-progress", (_event, state) => callback(state)),
});
