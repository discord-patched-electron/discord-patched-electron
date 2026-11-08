"use strict";
// Settings enables capture; updater activation refreshes existing grants.
// Status queries never prompt or capture.
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { execFile } = require("node:child_process");
const { createHash } = require("node:crypto");
const { Buffer } = require("node:buffer");
const { record } = require("./diagnostic-log.cjs");
const ROOT = "/usr/local/libexec/discord-patched-electron";
const SERVER = `${ROOT}/kms-server`;
const ACTION = "org.discord-patched-electron.capture";
const MAINTAINER = `${ROOT}/kms-maintenance`;
const PKEXEC = "/usr/bin/pkexec";
const PKCHECK = "/usr/bin/pkcheck";
const MARKER = "DPE-KMS-3\n";
let pending;
const digestCache = new Map();
function boundedFile(file, maximum, digest = false) {
  const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const info = fs.fstatSync(fd);
    if (!info.isFile() || info.size < 1 || info.size > maximum) throw Error("Invalid helper file");
    const identity = [info.dev, info.ino, info.size, info.mtimeMs, info.ctimeMs].join("/");
    const cached = digestCache.get(file);
    if (digest && cached?.identity === identity) return cached.value;
    const bytes = Buffer.alloc(info.size + 1);
    let length = 0;
    while (length < bytes.length) {
      const count = fs.readSync(fd, bytes, length, bytes.length - length, null);
      if (!count) break;
      length += count;
    }
    if (length !== info.size) throw Error("Changed helper file");
    if (!digest) return bytes.subarray(0, length);
    const value = createHash("sha256").update(bytes.subarray(0, length)).digest("hex");
    digestCache.set(file, { identity, value });
    if (digestCache.size > 8) digestCache.delete(digestCache.keys().next().value);
    return value;
  } finally {
    fs.closeSync(fd);
  }
}
function bundledDigest() {
  try {
    const manifest = JSON.parse(
      boundedFile(path.join(process.env.DPE_APP_ROOT || "", "capture/kms-server.json"), 4096),
    );
    return manifest.protocol === 6 &&
      manifest.permissionVersion === 4 &&
      /^[a-f0-9]{64}$/.test(manifest.sha256)
      ? manifest.sha256
      : null;
  } catch {
    return null;
  }
}
function runningGrant() {
  const executable = fs.readlinkSync("/proc/self/exe");
  if (
    !path.isAbsolute(executable) ||
    /[\r\n]/.test(executable) ||
    executable.endsWith(" (deleted)")
  )
    throw Error("Invalid running executable");
  const info = fs.statSync("/proc/self/exe", { bigint: true });
  const fields = [info.dev, info.ino, info.size, info.mtimeNs, info.ctimeNs].map(String);
  return MARKER + fields.join("\n") + "\n" + executable + "\n";
}
function trusted(file, executable = false, access = true) {
  try {
    const info = fs.lstatSync(file);
    if (!info.isFile() || info.uid !== 0 || info.mode & 0o022) return false;
    for (let parent = path.dirname(file); ; parent = path.dirname(parent)) {
      const directory = fs.lstatSync(parent);
      if (!directory.isDirectory() || directory.uid !== 0 || directory.mode & 0o022) return false;
      if (parent === path.dirname(parent)) break;
    }
    if (access) fs.accessSync(file, executable ? fs.constants.X_OK : fs.constants.R_OK);
    return true;
  } catch {
    return false;
  }
}
function maintenancePolicy() {
  try {
    const file = `${ROOT}/maintenance/${process.getuid()}`;
    if (!trusted(MAINTAINER, false, false) || !trusted(file)) return null;
    const value = JSON.parse(boundedFile(file, 16384));
    return value.schema === 1 && value.enabled === true ? value : null;
  } catch {
    return null;
  }
}
function authority() {
  const app = process.env.DPE_APP_ROOT || "";
  return {
    server: bundledDigest(),
    maintainer: boundedFile(
      path.join(app, "integration/video/kms-maintenance.py"),
      128 * 1024,
      true,
    ),
    installer: boundedFile(installer(), 128 * 1024, true),
  };
}
function installer() {
  return path.join(process.env.DPE_APP_ROOT || "", "integration/video/kms-permission-setup.py");
}
// Root cannot read a default AppImage FUSE mount. Stage only the permission
// installer and its bounded inputs on the host filesystem before authentication.
function stageInstaller() {
  if (!process.env.APPIMAGE) return { file: installer(), close() {} };
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "dpe-capture-setup-"));
  const close = () => fs.rmSync(directory, { recursive: true, force: true });
  try {
    for (const [relative, maximum] of [
      ["integration/video/kms-permission-setup.py", 128 * 1024],
      ["integration/video/kms-maintenance.py", 128 * 1024],
      ["integration/updater/release-signing-key.asc", 64 * 1024],
      ["capture/dpe-kms-server", 2 * 1024 * 1024],
      ["capture/kms-server.json", 4096],
      ["runtime-update.json", 64 * 1024],
    ]) {
      const target = path.join(directory, relative);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(
        target,
        boundedFile(path.join(process.env.DPE_APP_ROOT, relative), maximum),
        {
          mode: relative.endsWith(".py") ? 0o700 : 0o600,
          flag: "wx",
        },
      );
    }
    return { file: path.join(directory, "integration/video/kms-permission-setup.py"), close };
  } catch (error) {
    close();
    throw error;
  }
}
function readable(file, mode = fs.constants.R_OK) {
  try {
    fs.accessSync(file, mode);
    return true;
  } catch {
    return false;
  }
}
function granted(action = ACTION) {
  return new Promise((resolve) => {
    // Generic exec + --detail program is forbidden for unprivileged callers.
    // Our dedicated action's exec.path binds it to the fixed root-owned helper.
    execFile(
      PKCHECK,
      ["--action-id", action, "--process", String(process.pid)],
      { env: { PATH: "/usr/bin", LANG: "C" }, timeout: 3000, maxBuffer: 4096 },
      (error) => resolve(!error),
    );
  });
}
async function status() {
  let configured = false,
    current = false,
    binaryCurrent = true;
  const policy = maintenancePolicy();
  let maintenanceCurrent = false;
  if (policy) {
    configured = true;
    try {
      const expected = authority();
      maintenanceCurrent = Object.entries(expected).every(
        ([key, value]) => policy.current?.[key] === value,
      );
    } catch {
      /* Missing staged authority. */
    }
  }
  const result = (allowed, available, reason = null, canRevoke = false) => ({
    allowed,
    available,
    reason,
    configured,
    needsUpdate: configured && (!current || !binaryCurrent || !maintenanceCurrent),
    maintenanceInstalled: Boolean(policy),
    canRevoke,
  });
  if (process.platform !== "linux" || !Number.isSafeInteger(process.getuid?.()))
    return result(false, false, "authorization-unavailable");
  const marker = `${ROOT}/permissions/${process.getuid()}`;
  const expected = bundledDigest();
  try {
    if (trusted(marker)) {
      const contents = fs.readFileSync(marker, "utf8");
      configured ||= /^DPE-KMS-[123]\n/.test(contents);
      current = contents === runningGrant() && trusted(SERVER);
      if (current && expected) {
        try {
          binaryCurrent = boundedFile(SERVER, 2 * 1024 * 1024, true) === expected;
        } catch {
          binaryCurrent = false;
        }
      }
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
    expected &&
    readable(path.join(app, "capture/dpe-kms-server"), fs.constants.R_OK | fs.constants.X_OK) &&
    readable(path.join(app, "capture/kms-server.json")),
  );
  const upToDate = current && binaryCurrent && maintenanceCurrent;
  if (upToDate && (await granted())) return result(true, available, null, canRevoke);
  return result(
    false,
    available,
    !available
      ? "setup-unavailable"
      : upToDate
        ? "permission-denied"
        : configured
          ? "permission-update-required"
          : null,
    canRevoke,
  );
}
function setEnabled(enabled, initial = false) {
  if (typeof enabled !== "boolean")
    return Promise.reject(new Error("Invalid capture permission request."));
  if (pending) {
    if (pending.enabled === enabled && pending.initial === initial) return pending.promise;
    return pending.promise.then(() => setEnabled(enabled, initial));
  }
  const promise = (async () => {
    const current = await status();
    if (enabled ? current.allowed && !current.needsUpdate : !current.configured) return current;
    if (enabled ? !current.available : !current.canRevoke) return current;
    const silent = current.maintenanceInstalled && !initial;
    if (silent && !(await granted(ACTION + ".maintain")))
      return { ...current, reason: "authorization-unavailable" };
    const lease = silent
      ? require("./capture-kms-authorizer.cjs").createLease(MAINTAINER)
      : undefined;
    const staged = silent ? undefined : stageInstaller();
    return new Promise((resolve) => {
      const host = require("./capture-host.cjs");
      const execute = host.restricted() ? host.execFile : execFile;
      execute(
        PKEXEC,
        silent
          ? [
              "--disable-internal-agent",
              MAINTAINER,
              enabled ? "--refresh" : "--revoke",
              String(process.pid),
              process.env.DPE_APP_ROOT,
              lease.env.DPE_KMS_AUTH_SOCKET,
              lease.env.DPE_KMS_AUTH_TOKEN,
            ]
          : [
              "--disable-internal-agent",
              staged.file,
              ...(enabled ? ["--allow", String(process.pid)] : ["--revoke"]),
            ],
        { env: { PATH: "/usr/bin", LANG: "C" }, timeout: silent ? 60000 : 120000, maxBuffer: 4096 },
        async (error, _stdout, stderr) => {
          lease?.close();
          staged?.close();
          let value = await status();
          // Both the new action and its per-user rule reload asynchronously.
          // Revocation is immediate at the helper's marker, even before reload.
          for (
            let i = 0;
            !error && enabled && (!value.allowed || value.needsUpdate) && value.available && i < 25;
            i++
          ) {
            await new Promise((done) => setTimeout(done, 200));
            value = await status();
          }
          if (enabled ? value.allowed && !value.needsUpdate : !value.configured) resolve(value);
          else {
            record("capture-permission-failed", {
              enabled,
              silent,
              code: error?.code,
              signal: error?.signal,
              stderr: typeof stderr === "string" ? stderr.slice(-4096) : "",
              status: value,
            });
            resolve({
              ...value,
              reason:
                error?.code === 126
                  ? "authorization-cancelled"
                  : error?.code === 127
                    ? "authorization-unavailable"
                    : silent
                      ? "update-not-approved"
                      : "setup-failed",
            });
          }
        },
      );
    });
  })().finally(() => {
    pending = undefined;
  });
  pending = { enabled, initial, promise };
  return promise;
}
let maintenance;
function maintain() {
  // Run once when this application/runtime becomes active, never while a
  // background download is merely pending. Cancellation is remembered for this
  // target so relaunches do not continually reopen authentication dialogs.
  maintenance ||= (async () => {
    const current = await status();
    if (!current.configured || !current.needsUpdate || !current.available) return current;
    if (current.maintenanceInstalled) return setEnabled(true);
    const profile = process.env.DPE_PROFILE_DIR;
    if (!profile || !path.isAbsolute(profile)) return current;
    const target = JSON.stringify(authority()) + "\n" + runningGrant();
    const record = path.join(profile, "dpe-kms-maintenance.json");
    try {
      if (JSON.parse(fs.readFileSync(record, "utf8")).target === target) return current;
    } catch {
      /* First attempt or a damaged unprivileged maintenance record. */
    }
    const temporary = record + "." + process.pid + ".tmp";
    try {
      fs.writeFileSync(temporary, JSON.stringify({ target }) + "\n", { mode: 0o600, flag: "wx" });
      fs.renameSync(temporary, record);
    } catch {
      fs.rmSync(temporary, { force: true });
      return current;
    }
    return setEnabled(true);
  })();
  return maintenance;
}
module.exports = {
  status,
  maintain,
  allow: () => setEnabled(true, true),
  revoke: () => setEnabled(false),
  setEnabled,
};
