const assert = require("node:assert/strict");
const fs = require("node:fs");
const net = require("node:net");
const { once } = require("node:events");
const { test } = require("node:test");
const { createLease } = require("../../integration/updater/capture-kms-authorizer.cjs");

test("worker gates reject missing capabilities, admit one session, and revoke on teardown", async () => {
  const lease = createLease(process.execPath);
  const endpoint = lease.env.DPE_KMS_AUTH_SOCKET;
  const connect = async () => {
    const socket = net.createConnection(endpoint);
    await once(socket, "connect");
    return socket;
  };
  try {
    const bad = await connect();
    const rejected = once(bad, "close");
    bad.write("wrong\n");
    await rejected;
    const client = await connect();
    const reply = once(client, "data");
    const token = lease.env.DPE_KMS_AUTH_TOKEN;
    client.write(token.slice(0, 16));
    client.write(token.slice(16) + "\n");
    assert.equal((await reply)[0].toString(), fs.realpathSync(process.execPath) + "\n");
    const duplicate = await connect();
    const denied = once(duplicate, "close");
    duplicate.write(token + "\n");
    await denied;
    const disconnected = once(client, "close");
    client.resume();
    lease.close();
    await disconnected;
    assert.equal(fs.existsSync(endpoint), false);
  } finally {
    lease.close();
  }
});
