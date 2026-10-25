// Compatibility transforms run in memory; updater-owned ASARs stay unchanged.
function replace(source, before, after) {
  if (source.split(before).length !== 2) {
    throw new Error(`Unsupported Discord client layout: ${before}`);
  }
  return source.replace(before, after);
}

function desktop(source, adapter) {
  // This installation already has a module updater. Do not offer Discord's
  // legacy-to-native migration, which would download and launch stock Electron.
  source = replace(
    source,
    "event.returnValue=injectedUpdater?.getUpdater()!=null",
    "event.returnValue=!0",
  );
  source = replace(
    source,
    "return null!=newUpdater?newUpdater.committedModulePaths.get(name)??null:null",
    "return null!=newUpdater?newUpdater.committedModulePaths.get(name)??null:injectedModuleUpdater?.getModulePath(name)??null",
  );
  // Same validated IPC replacement as scripts/patch_desktop_core.py.
  const before =
    "electron.ipcMain.on(SAFE_STORAGE_IS_ENCRYPTION_AVAILABLE,event=>{event.returnValue=null!=electron.safeStorage&&electron.safeStorage.isEncryptionAvailable()})," +
    'electron.ipcMain.on(SAFE_STORAGE_ENCRYPT_STRING,(event,plainText)=>{null!=plainText?event.returnValue=electron.safeStorage.encryptString(plainText).toString("base64"):event.returnValue=null}),' +
    'electron.ipcMain.on(SAFE_STORAGE_DECRYPT_STRING,(event,encrypted)=>{if(null!=encrypted)try{event.returnValue=electron.safeStorage.decryptString(Buffer.from(encrypted,"base64"))}catch{event.returnValue=null}else event.returnValue=null})';
  return replace(
    source,
    before,
    `require(${JSON.stringify(adapter)}).install(electron,{SAFE_STORAGE_IS_ENCRYPTION_AVAILABLE,SAFE_STORAGE_ENCRYPT_STRING,SAFE_STORAGE_DECRYPT_STRING})`,
  );
}

module.exports = { desktop };

if (require.main === module) {
  const fs = require("node:fs");
  const path = require("node:path");
  desktop(
    fs.readFileSync(path.join(process.argv[2], "core.asar/bundle.js"), "utf8"),
    path.join(__dirname, "../desktop/safe-storage.cjs"),
  );
}
