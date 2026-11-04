"use strict";
// Trusted DPE settings owns permission changes. Queries never prompt or capture.
const fs = require("node:fs");
const path = require("node:path");
const { execFile } = require("node:child_process");
const ROOT = "/usr/local/libexec/discord-patched-electron";
const SERVER = `${ROOT}/kms-server`;
const ACTION = "org.discord-patched-electron.capture";
const PKEXEC = "/usr/bin/pkexec";
const PKCHECK = "/usr/bin/pkcheck";
const MARKER = "DPE-KMS-2\n";
let pending;
function trusted(file, executable = false) {
  try {
    const info = fs.lstatSync(file);
    if (!info.isFile() || info.uid !== 0 || info.mode & 0o022) return false;
    for (let parent = path.dirname(file); ; parent = path.dirname(parent)) {
      const directory = fs.lstatSync(parent);
      if (!directory.isDirectory() || directory.uid !== 0 || directory.mode & 0o022) return false;
      if (parent === path.dirname(parent)) break;
    }
    fs.accessSync(file, executable ? fs.constants.X_OK : fs.constants.R_OK);
    return true;
  } catch {
    return false;
  }
}
function installer() {
  return path.join(process.env.DPE_APP_ROOT || "", "integration/video/kms-permission-setup.py");
}
function readable(file, mode = fs.constants.R_OK) {
  try {
    fs.accessSync(file, mode);
    return true;
  } catch {
    return false;
  }
}
function granted() {
  return new Promise((resolve) => {
    // Generic exec + --detail program is forbidden for unprivileged callers.
    // Our dedicated action's exec.path binds it to the fixed root-owned helper.
    execFile(
      PKCHECK,
      ["--action-id", ACTION, "--process", String(process.pid)],
      { env: { PATH: "/usr/bin", LANG: "C" }, timeout: 3000, maxBuffer: 4096 },
      (error) => resolve(!error),
    );
  });
}
async function status() {
  let configured = false,
    current = false;
  const result = (allowed, available, reason = null, canRevoke = false) => ({
    allowed,
    available,
    reason,
    configured,
    needsUpdate: configured && !current,
    canRevoke,
  });
  if (process.platform !== "linux" || !Number.isSafeInteger(process.getuid?.()))
    return result(false, false, "authorization-unavailable");
  const marker = `${ROOT}/permissions/${process.getuid()}`;
  try {
    if (trusted(marker)) {
      const contents = fs.readFileSync(marker, "utf8");
      configured = /^DPE-KMS-[12]\n$/.test(contents);
      current = contents === MARKER && trusted(SERVER, true);
    }
  } catch {
    /* The administrator may be revoking the grant. */
  }
  try {
    if (
      /^NoNewPrivs:\s+1$/m.test(fs.readFileSync("/proc/self/status", "utf8")) &&
      !(await require("./capture-host.cjs").available())
    )
      return result(false, false, "permission-blocked");
  } catch {
    return result(false, false, "permission-blocked");
  }
  if (!trusted(PKEXEC, true) || !trusted(PKCHECK, true))
    return result(false, false, "authorization-unavailable");
  const app = process.env.DPE_APP_ROOT;
  const setup =
    app && path.isAbsolute(app) && readable(installer(), fs.constants.R_OK | fs.constants.X_OK);
  const canRevoke = configured && Boolean(setup);
  const available = Boolean(
    setup &&
    readable(path.join(app, "capture/dpe-kms-server"), fs.constants.R_OK | fs.constants.X_OK) &&
    readable(path.join(app, "capture/kms-server.json")),
  );
  if (current && (await granted())) return result(true, available, null, canRevoke);
  return result(
    false,
    available,
    !available
      ? "setup-unavailable"
      : current
        ? "permission-denied"
        : configured
          ? "permission-update-required"
          : null,
    canRevoke,
  );
}
function setEnabled(enabled) {
  if (typeof enabled !== "boolean")
    return Promise.reject(new Error("Invalid capture permission request."));
  if (pending) {
    if (pending.enabled === enabled) return pending.promise;
    return pending.promise.then(() => setEnabled(enabled));
  }
  const promise = (async () => {
    const current = await status();
    if (enabled ? current.allowed : !current.configured) return current;
    if (enabled ? !current.available : !current.canRevoke) return current;
    return new Promise((resolve) => {
      const host = require("./capture-host.cjs");
      const execute = host.restricted() ? host.execFile : execFile;
      execute(
        PKEXEC,
        ["--disable-internal-agent", installer(), enabled ? "--allow" : "--revoke"],
        { env: { PATH: "/usr/bin", LANG: "C" }, timeout: 120000, maxBuffer: 4096 },
        async (error) => {
          let value = await status();
          // Both the new action and its per-user rule reload asynchronously.
          // Revocation is immediate at the helper's marker, even before reload.
          for (let i = 0; !error && enabled && !value.allowed && value.available && i < 25; i++) {
            await new Promise((done) => setTimeout(done, 200));
            value = await status();
          }
          if (enabled ? value.allowed : !value.configured) resolve(value);
          else
            resolve({
              ...value,
              reason:
                error?.code === 126
                  ? "authorization-cancelled"
                  : error?.code === 127
                    ? "authorization-unavailable"
                    : "setup-failed",
            });
        },
      );
    });
  })().finally(() => {
    pending = undefined;
  });
  pending = { enabled, promise };
  return promise;
}
module.exports = {
  status,
  allow: () => setEnabled(true),
  revoke: () => setEnabled(false),
  setEnabled,
};
