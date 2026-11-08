const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const source = fs.readFileSync(
  path.join(__dirname, "../../integration/updater/capture-permission.cjs"),
  "utf8",
);
const root = "/usr/local/libexec/discord-patched-electron";
function fixture(options = {}) {
  let configured = options.allowed === true || options.legacy === true;
  let current = options.allowed === true;
  let hasMaintenance = options.maintenance === true;
  let outdated = options.outdated === true;
  let generation = 0;
  let nextDescriptor = 10;
  const descriptors = new Map();
  const bundled = Buffer.from("reviewed Rust capture helper fixture");
  const expected = require("node:crypto").createHash("sha256").update(bundled).digest("hex");
  function contents(file) {
    if (options.missing?.includes(file)) throw Error("missing");
    if (file === "/app/capture/kms-server.json")
      return Buffer.from(
        JSON.stringify({
          protocol: 6,
          permissionVersion: 4,
          sha256: expected,
        }),
      );
    if (file === `${root}/maintenance/1000`) {
      if (!hasMaintenance) throw Error("No maintenance authority");
      const hash = require("node:crypto")
        .createHash("sha256")
        .update("fixture component")
        .digest("hex");
      return Buffer.from(
        JSON.stringify({
          schema: 1,
          enabled: true,
          current: { server: expected, maintainer: hash, installer: hash },
        }),
      );
    }
    if (
      [
        "/app/integration/video/kms-maintenance.py",
        "/app/integration/video/kms-permission-setup.py",
      ].includes(file)
    )
      return Buffer.from("fixture component");
    if (file === `${root}/kms-server`)
      return outdated ? Buffer.from("older C capture helper fixture") : bundled;
    throw Error("Unexpected bounded read");
  }
  const grant = "DPE-KMS-3\n1\n2\n100\n4\n5\n/fixture/discord\n";
  const records = options.records || new Map();
  const calls = [];
  let finish;
  let hostCalls = 0;
  const module = { exports: {} };
  const directories = new Set([
    "/",
    "/usr",
    "/usr/bin",
    "/usr/local",
    "/usr/local/libexec",
    root,
    `${root}/permissions`,
    `${root}/maintenance`,
  ]);
  vm.runInNewContext(source, {
    module,
    setTimeout: (fn) => fn(),
    process: {
      platform: "linux",
      pid: 123,
      getuid: () => 1000,
      env: { DPE_APP_ROOT: "/app", DPE_PROFILE_DIR: "/profile" },
    },
    require: contextRequire,
  });
  function contextRequire(name) {
    if (name === "./diagnostic-log.cjs") return { record() {} };
    if (name === "node:fs")
      return {
        constants: fs.constants,
        readlinkSync: () => "/fixture/discord",
        statSync: () => ({ dev: 1n, ino: 2n, size: 100n, mtimeNs: 4n, ctimeNs: 5n }),
        writeFileSync: (file, value) => records.set(file, value),
        renameSync: (source, destination) => {
          records.set(destination, records.get(source));
          records.delete(source);
        },
        rmSync: (file) => records.delete(file),
        openSync(file, flags) {
          assert.ok(flags & fs.constants.O_NOFOLLOW);
          contents(file);
          const fd = nextDescriptor++;
          descriptors.set(fd, { file, offset: 0 });
          return fd;
        },
        fstatSync(fd) {
          const file = descriptors.get(fd).file;
          return {
            isFile: () => true,
            size: options.oversized === file ? 3 * 1024 * 1024 : contents(file).length,
            dev: 1,
            ino:
              file === `${root}/kms-server`
                ? 1
                : file.endsWith("kms-maintenance.py")
                  ? 2
                  : file.endsWith("kms-permission-setup.py")
                    ? 3
                    : 4,
            mtimeMs: generation,
            ctimeMs: generation,
          };
        },
        readSync(fd, output, offset, count) {
          const value = descriptors.get(fd);
          const bytes = contents(value.file).subarray(value.offset, value.offset + count);
          bytes.copy(output, offset);
          value.offset += bytes.length;
          return bytes.length;
        },
        closeSync(fd) {
          assert.equal(descriptors.delete(fd), true);
        },
        lstatSync(file) {
          if (
            (file === `${root}/kms-maintenance` || file === `${root}/maintenance/1000`) &&
            !hasMaintenance
          )
            throw Error("missing authority");
          if (options.missing?.includes(file) || (file.startsWith(root) && !configured))
            throw Error("missing");
          return {
            isFile: () => !directories.has(file),
            isDirectory: () => directories.has(file),
            uid: options.untrusted === file ? 1000 : 0,
            mode:
              (directories.has(file) ? 0o40755 : 0o100755) |
              (options.writable === file ? 0o002 : 0),
          };
        },
        accessSync(file) {
          if (options.missing?.includes(file)) throw Error("missing");
        },
        readFileSync(file) {
          if (file === "/proc/self/status") return `NoNewPrivs:\t${options.blocked ? 1 : 0}\n`;
          if (file === `${root}/maintenance/1000`) return contents(file).toString();
          if (file === `${root}/permissions/1000`)
            return current
              ? options.wrongBinary
                ? grant.replace("/fixture/discord", "/old/discord")
                : grant
              : "DPE-KMS-1\n";
          if (records.has(file)) return records.get(file);
          throw Error("unexpected read");
        },
      };
    if (name === "./capture-kms-authorizer.cjs")
      return {
        createLease: () => ({
          env: { DPE_KMS_AUTH_SOCKET: "/private/gate", DPE_KMS_AUTH_TOKEN: "capability" },
          close: () => calls.push({ file: "lease-close" }),
        }),
      };
    if (name === "./capture-host.cjs")
      return {
        available: async () => options.hostService === true,
        restricted: () => Boolean(options.blocked),
        execFile: (...args) => {
          hostCalls++;
          return contextRequire("node:child_process").execFile(...args);
        },
      };
    if (name === "node:child_process")
      return {
        execFile(file, args, config, callback) {
          calls.push({ file, args: Array.from(args), config });
          if (file === "/usr/bin/pkcheck") {
            assert.equal(
              args.includes("--detail"),
              false,
              "unprivileged callers cannot pass action details",
            );
            callback(
              options.denied ||
                (options.maintenanceDenied &&
                  args.includes("org.discord-patched-electron.capture.maintain"))
                ? { code: 1 }
                : null,
            );
          } else
            finish = (error) => {
              if (!error) {
                configured = current = args.includes("--allow") || args.includes("--refresh");
                if (configured) hasMaintenance = true;
                if (current) outdated = false;
                generation++;
              }
              callback(error);
            };
        },
      };
    return require(name);
  }
  return {
    api: module.exports,
    calls,
    hostCalls: () => hostCalls,
    records,
    finish: (error) => finish(error),
  };
}
const flush = () => new Promise((resolve) => setImmediate(resolve));
test("permission status uses its dedicated action with no forbidden details or GSR dependency", async () => {
  const initial = fixture({ missing: ["/usr/bin/gsr-kms-server"] });
  assert.equal((await initial.api.status()).available, true);
  assert.equal(initial.calls.length, 0);
  const existing = fixture({ allowed: true, maintenance: true });
  assert.equal((await existing.api.status()).allowed, true);
  assert.deepEqual(existing.calls[0].args, [
    "--action-id",
    "org.discord-patched-electron.capture",
    "--process",
    "123",
  ]);
  assert.equal((await fixture({ allowed: true, denied: true }).api.status()).allowed, false);
  assert.equal(
    (await fixture({ allowed: true, writable: "/usr/local" }).api.status()).allowed,
    false,
  );
  const legacy = await fixture({ legacy: true }).api.status();
  assert.equal(legacy.allowed, false);
  assert.equal(legacy.configured, true);
  assert.equal(legacy.needsUpdate, true);
  assert.equal(legacy.canRevoke, true);
});
test("explicit allow/revoke serialize authorization and verify actual permission state", async () => {
  const f = fixture();
  const first = f.api.allow(),
    second = f.api.allow();
  await flush();
  assert.equal(f.calls.length, 1);
  assert.deepEqual(f.calls[0].args, [
    "--disable-internal-agent",
    "/app/integration/video/kms-permission-setup.py",
    "--allow",
    "123",
  ]);
  f.finish(null);
  assert.equal((await first).allowed, true);
  assert.equal((await second).allowed, true);
  const revoke = f.api.revoke();
  await flush();
  assert.equal(
    f.calls.findLast((c) => c.file === "/usr/bin/pkexec").args.includes("--revoke"),
    true,
  );
  f.finish(null);
  const state = await revoke;
  assert.equal(state.allowed, false);
  assert.equal(state.configured, false);
});
test("blocked/missing assets/cancelled setup preserve state and never imply a revoked grant", async () => {
  const blocked = fixture({ blocked: true });
  assert.equal((await blocked.api.allow()).reason, "permission-blocked");
  assert.equal(blocked.calls.length, 0);
  assert.equal(
    (await fixture({ missing: ["/app/capture/dpe-kms-server"] }).api.status()).reason,
    "setup-unavailable",
  );
  const f = fixture({ allowed: true, maintenance: true });
  const attempt = f.api.revoke();
  await flush();
  f.finish({ code: 126 });
  const state = await attempt;
  assert.equal(state.reason, "authorization-cancelled");
  assert.equal(state.allowed, true);
});

test("legacy installation migrates only on explicit allow and blocked grants remain installed", async () => {
  const legacy = fixture({ legacy: true });
  assert.equal((await legacy.api.status()).configured, true);
  assert.equal(legacy.calls.length, 0);
  const allow = legacy.api.allow();
  await flush();
  assert.equal(legacy.calls[0].args.at(-2), "--allow");
  legacy.finish(null);
  assert.equal((await allow).allowed, true);
  const blocked = fixture({ allowed: true, blocked: true });
  const status = await blocked.api.status();
  assert.equal(status.configured, true);
  assert.equal(status.allowed, false);
  assert.equal(status.canRevoke, false);
  assert.equal(status.reason, "permission-blocked");
  assert.equal(blocked.calls.length, 0);
  const denied = await fixture({ allowed: true, maintenance: true, denied: true }).api.status();
  assert.equal(denied.reason, "permission-denied");
  assert.equal(denied.needsUpdate, false);
});

test("restricted launches use a verified user service for explicit permission changes", async () => {
  const f = fixture({ blocked: true, hostService: true });
  assert.equal((await f.api.status()).available, true);
  assert.equal(f.calls.length, 0);
  const allow = f.api.allow();
  await flush();
  assert.equal(f.hostCalls(), 1);
  assert.equal(f.calls[0].file, "/usr/bin/pkexec");
  f.finish(null);
  assert.equal((await allow).allowed, true);
  const revoke = f.api.revoke();
  await flush();
  assert.equal(f.hostCalls(), 2);
  f.finish(null);
  assert.equal((await revoke).configured, false);
});

test("a helper update keeps its grant configured but disables capture until explicit allow", async () => {
  const f = fixture({ allowed: true, outdated: true });
  const before = await f.api.status();
  assert.equal(before.allowed, false);
  assert.equal(before.configured, true);
  assert.equal(before.reason, "permission-update-required");
  assert.equal(before.needsUpdate, true);
  assert.equal(
    f.calls.some((c) => c.file === "/usr/bin/pkexec"),
    false,
  );
  const update = f.api.allow();
  await flush();
  assert.equal(f.calls.at(-1).args.at(-2), "--allow");
  f.finish(null);
  const after = await update;
  assert.equal(after.allowed, true);
  assert.equal(after.needsUpdate, false);
  assert.equal(
    f.calls.some((c) => c.args.at(-1) === "--revoke"),
    false,
  );
  const cancelled = fixture({ allowed: true, outdated: true });
  const attempt = cancelled.api.allow();
  await flush();
  cancelled.finish({ code: 126 });
  const state = await attempt;
  assert.equal(state.allowed, false);
  assert.equal(state.configured, true);
  assert.equal(state.needsUpdate, true);
  assert.equal(state.reason, "authorization-cancelled");
});

test("oversized or unavailable helper manifests cannot authorize an update", async () => {
  const missing = await fixture({
    allowed: true,
    missing: ["/app/capture/kms-server.json"],
  }).api.status();
  assert.equal(missing.allowed, false);
  assert.equal(missing.configured, true);
  assert.equal(missing.available, false);
  const f = fixture({ allowed: true, outdated: true, oversized: `${root}/kms-server` });
  const state = await f.api.status();
  assert.equal(state.allowed, false);
  assert.equal(state.configured, true);
  assert.equal(state.needsUpdate, true);
});

test("activation refreshes an existing outdated helper once without enabling fresh grants", async () => {
  const fresh = fixture();
  assert.equal((await fresh.api.maintain()).configured, false);
  assert.equal(fresh.calls.length, 0);
  const f = fixture({ allowed: true, outdated: true, maintenance: true });
  const first = f.api.maintain();
  const second = f.api.maintain();
  await flush();
  assert.equal(f.calls.filter((c) => c.file === "/usr/bin/pkexec").length, 1);
  f.finish(null);
  assert.equal((await first).needsUpdate, false);
  assert.equal((await second).needsUpdate, false);
});
test("cancelled activation maintenance is remembered, while manual retry remains available", async () => {
  const records = new Map();
  const f = fixture({ legacy: true, records });
  const attempt = f.api.maintain();
  await flush();
  f.finish({ code: 126 });
  assert.equal((await attempt).reason, "authorization-cancelled");
  const restarted = fixture({ legacy: true, records });
  assert.equal((await restarted.api.maintain()).needsUpdate, true);
  assert.equal(
    restarted.calls.some((c) => c.file === "/usr/bin/pkexec"),
    false,
  );
  const manual = restarted.api.allow();
  await flush();
  restarted.finish(null);
  assert.equal((await manual).needsUpdate, false);
});
test("a grant for another executable is configured but cannot authorize the running Discord", async () => {
  const f = fixture({ allowed: true, wrongBinary: true });
  const value = await f.api.status();
  assert.equal(value.configured, true);
  assert.equal(value.allowed, false);
  assert.equal(value.needsUpdate, true);
  assert.equal(value.reason, "permission-update-required");
});

test("installed maintenance refreshes without invoking the user-writable installer", async () => {
  const f = fixture({ allowed: true, outdated: true, maintenance: true });
  const attempt = f.api.maintain();
  await flush();
  const command = f.calls.findLast((c) => c.file === "/usr/bin/pkexec");
  assert.equal(command.args[1], root + "/kms-maintenance");
  assert.equal(command.args[2], "--refresh");
  assert.equal(command.args.includes("/app/integration/video/kms-permission-setup.py"), false);
  f.finish(null);
  assert.equal((await attempt).needsUpdate, false);
});
test("unapproved silent updates fail closed without falling back to authentication", async () => {
  const f = fixture({ allowed: true, outdated: true, maintenance: true });
  const attempt = f.api.maintain();
  await flush();
  f.finish({ code: 1 });
  assert.equal((await attempt).reason, "update-not-approved");
  assert.equal(f.calls.filter((c) => c.file === "/usr/bin/pkexec").length, 1);
  assert.equal(
    f.calls.some((c) => c.args?.includes("/app/integration/video/kms-permission-setup.py")),
    false,
  );
});

test("missing maintenance authorization never falls back to a prompting pkexec action", async () => {
  const f = fixture({ allowed: true, outdated: true, maintenance: true, maintenanceDenied: true });
  assert.equal((await f.api.maintain()).reason, "authorization-unavailable");
  assert.equal(
    f.calls.some((c) => c.file === "/usr/bin/pkexec"),
    false,
  );
  assert.equal(
    f.calls.some((c) => c.args?.includes("--allow-user-interaction")),
    false,
  );
});
