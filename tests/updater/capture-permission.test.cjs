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
  ]);
  vm.runInNewContext(source, {
    module,
    setTimeout: (fn) => fn(),
    process: { platform: "linux", pid: 123, getuid: () => 1000, env: { DPE_APP_ROOT: "/app" } },
    require: contextRequire,
  });
  function contextRequire(name) {
    if (name === "node:fs")
      return {
        constants: fs.constants,
        lstatSync(file) {
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
          if (file === `${root}/permissions/1000`) return current ? "DPE-KMS-2\n" : "DPE-KMS-1\n";
          throw Error("unexpected read");
        },
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
            callback(options.denied ? { code: 1 } : null);
          } else
            finish = (error) => {
              if (!error) configured = current = args.at(-1) === "--allow";
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
    finish: (error) => finish(error),
  };
}
const flush = () => new Promise((resolve) => setImmediate(resolve));
test("permission status uses its dedicated action with no forbidden details or GSR dependency", async () => {
  const initial = fixture({ missing: ["/usr/bin/gsr-kms-server"] });
  assert.equal((await initial.api.status()).available, true);
  assert.equal(initial.calls.length, 0);
  const existing = fixture({ allowed: true });
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
  ]);
  f.finish(null);
  assert.equal((await first).allowed, true);
  assert.equal((await second).allowed, true);
  const revoke = f.api.revoke();
  await flush();
  assert.equal(f.calls.at(-1).args.at(-1), "--revoke");
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
  const f = fixture({ allowed: true });
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
  assert.equal(legacy.calls[0].args.at(-1), "--allow");
  legacy.finish(null);
  assert.equal((await allow).allowed, true);
  const blocked = fixture({ allowed: true, blocked: true });
  const status = await blocked.api.status();
  assert.equal(status.configured, true);
  assert.equal(status.allowed, false);
  assert.equal(status.canRevoke, false);
  assert.equal(status.reason, "permission-blocked");
  assert.equal(blocked.calls.length, 0);
  const denied = await fixture({ allowed: true, denied: true }).api.status();
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
