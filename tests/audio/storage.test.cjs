const { test } = require("node:test");
const assert = require("node:assert/strict");
const { install } = require("../../integration/desktop/safe-storage.cjs");
const events = {
  SAFE_STORAGE_IS_ENCRYPTION_AVAILABLE: "available",
  SAFE_STORAGE_ENCRYPT_STRING: "encrypt",
  SAFE_STORAGE_DECRYPT_STRING: "decrypt",
};
function fixture(safeStorage) {
  const handlers = {};
  install({ safeStorage, ipcMain: { on: (name, handler) => (handlers[name] = handler) } }, events);
  return async (name, value) => {
    const event = {};
    await handlers[name](event, value);
    return event.returnValue;
  };
}
test("async encryption/decryption preserve Discord IPC values", async () => {
  const call = fixture({
    isAsyncEncryptionAvailable: async () => true,
    encryptStringAsync: async (s) => Buffer.from("cipher:" + s),
    decryptStringAsync: async (b) => ({ result: b.toString().slice(7), shouldReEncrypt: false }),
  });
  assert.equal(await call("available"), true);
  const encrypted = await call("encrypt", "test");
  assert.equal(await call("decrypt", encrypted), "test");
  assert.equal(await call("encrypt", null), null);
});
test("key store failures reply without plaintext fallback or uncaught exceptions", async () => {
  const fail = async () => {
    throw Error("key store locked");
  };
  const call = fixture({
    isAsyncEncryptionAvailable: fail,
    encryptStringAsync: fail,
    decryptStringAsync: fail,
  });
  assert.equal(await call("available"), false);
  assert.equal(await call("encrypt", "do not store plaintext"), null);
  assert.equal(await call("decrypt", "invalid"), null);
});
test("older synchronous APIs remain usable", async () => {
  const call = fixture({
    isEncryptionAvailable: () => true,
    encryptString: (s) => Buffer.from(s),
    decryptString: (b) => b.toString(),
  });
  assert.equal(await call("available"), true);
  assert.equal(await call("decrypt", await call("encrypt", "synthetic")), "synthetic");
});
