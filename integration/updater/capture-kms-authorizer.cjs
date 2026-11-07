"use strict";
// A worker capability is served by the real Discord process. The privileged
// helper authenticates that process with SO_PEERCRED and the root-owned grant.
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { randomBytes } = require("node:crypto");
function createLease(file) {
  const executable = fs.realpathSync(file);
  if (/[\r\n]/.test(executable)) throw Error("Invalid capture worker path");
  const application =
    path.basename(executable) === "kms-maintenance"
      ? fs.realpathSync(process.env.DPE_APP_ROOT)
      : undefined;
  if (application && /[\r\n]/.test(application)) throw Error("Invalid application path");
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "dpe-kms-auth-"));
  const endpoint = path.join(directory, "socket");
  const token = randomBytes(32).toString("hex");
  const clients = new Set();
  let closed = false;
  let active;
  const server = net.createServer((client) => {
    if (closed || clients.size >= 4) return client.destroy();
    clients.add(client);
    client.on("error", () => client.destroy());
    client.on("close", () => {
      clients.delete(client);
      if (active === client) active = undefined;
    });
    client.setTimeout(2000, () => client.destroy());
    let request = "";
    const receive = (bytes) => {
      request += bytes.toString("ascii");
      if (request.length > 65) return client.destroy();
      if (!request.endsWith("\n")) return;
      if (active || request !== token + "\n") return client.destroy();
      active = client;
      client.off("data", receive);
      client.setTimeout(0);
      client.on("data", () => client.destroy());
      client.write(executable + "\n" + (application ? application + "\n" : ""));
    };
    client.on("data", receive);
  });
  const close = () => {
    if (closed) return;
    closed = true;
    for (const client of clients) client.destroy();
    server.close();
    fs.rmSync(directory, { recursive: true, force: true });
  };
  server.on("error", close);
  try {
    server.listen(endpoint);
    server.unref();
  } catch (error) {
    close();
    throw error;
  }
  return { env: { DPE_KMS_AUTH_SOCKET: endpoint, DPE_KMS_AUTH_TOKEN: token }, close };
}
module.exports = { createLease };
