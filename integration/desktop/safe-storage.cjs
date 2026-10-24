"use strict";

// Keep Discord's synchronous renderer IPC contract, while the main process
// awaits Electron 46's asynchronous OS key store. Never substitute plaintext.
function install({ ipcMain, safeStorage }, events) {
  function listen(channel, fallback, operation) {
    ipcMain.on(channel, async (event, value) => {
      let result = fallback;
      try {
        result = await operation(value);
      } catch {
        /* A locked/unavailable key store must still release sendSync. */
      }
      // Electron retains the synchronous reply until returnValue is assigned.
      try {
        event.returnValue = result;
      } catch {
        /* Renderer exited meanwhile. */
      }
    });
  }
  listen(events.SAFE_STORAGE_IS_ENCRYPTION_AVAILABLE, false, () => {
    if (!safeStorage) return false;
    return typeof safeStorage.isAsyncEncryptionAvailable === "function"
      ? safeStorage.isAsyncEncryptionAvailable()
      : safeStorage.isEncryptionAvailable();
  });
  listen(events.SAFE_STORAGE_ENCRYPT_STRING, null, async (value) => {
    if (value == null) return null;
    const encrypted =
      typeof safeStorage.encryptStringAsync === "function"
        ? await safeStorage.encryptStringAsync(value)
        : safeStorage.encryptString(value);
    return encrypted.toString("base64");
  });
  listen(events.SAFE_STORAGE_DECRYPT_STRING, null, async (value) => {
    if (value == null) return null;
    const encrypted = Buffer.from(value, "base64");
    return typeof safeStorage.decryptStringAsync === "function"
      ? (await safeStorage.decryptStringAsync(encrypted)).result
      : safeStorage.decryptString(encrypted);
  });
}
module.exports = { install };
