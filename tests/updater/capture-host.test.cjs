const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { test } = require("node:test");
const source = fs.readFileSync(
  require.resolve("../../integration/updater/capture-host.cjs"),
  "utf8",
);
function fixture(options = {}) {
  const calls = [],
    module = { exports: {} };
  vm.runInNewContext(source, {
    module,
    Date,
    process: {
      pid: 123,
      getuid: () => 1000,
      env: {
        XDG_RUNTIME_DIR: "/run/user/1000",
        WAYLAND_DISPLAY: "wayland-1",
        LD_PRELOAD: "unsafe",
      },
    },
    require(name) {
      if (name === "node:fs")
        return {
          constants: fs.constants,
          readFileSync: () => `NoNewPrivs:\t${options.restricted === false ? 0 : 1}\n`,
          accessSync() {},
          lstatSync(file) {
            return {
              uid: options.untrusted ? 1000 : 0,
              mode: 0o755,
              isFile: () => file.startsWith("/usr/bin/"),
              isDirectory: () => !file.startsWith("/usr/bin/"),
            };
          },
        };
      if (name === "node:crypto") return { randomUUID: () => "unique" };
      if (name === "node:child_process")
        return {
          execFile(file, args, config, done) {
            calls.push({ file, args: Array.from(args), config });
            done(options.error || null, `NoNewPrivs:\t${options.hostRestricted ? 1 : 0}\n`);
          },
          spawn(file, args, config) {
            const child = {};
            calls.push({ file, args: Array.from(args), config, child });
            return child;
          },
        };
      return require(name);
    },
  });
  return { api: module.exports, calls };
}
test("host probe requires trusted tools and an unrestricted user service and caches only briefly", async () => {
  const f = fixture();
  assert.equal(await f.api.available(), true);
  assert.equal(await f.api.available(), true);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].file, "/usr/bin/systemd-run");
  assert.deepEqual(f.calls[0].args.slice(-3), ["--", "/usr/bin/cat", "/proc/self/status"]);
  assert.equal(f.calls[0].config.env.LD_PRELOAD, undefined);
  for (const options of [
    { untrusted: true },
    { hostRestricted: true },
    { error: Error("no manager") },
  ])
    assert.equal(await fixture(options).api.available(), false);
});
test("restricted workers preserve literal arguments, forward session variables and stop only their own unit", () => {
  const f = fixture();
  const child = f.api.spawn("/app/dpe-kms-source", ["literal$VALUE"], {
    env: {
      WAYLAND_DISPLAY: "wayland-2",
      LD_PRELOAD: "unsafe",
      DPE_KMS_AUTH_SOCKET: "/private/socket",
      DPE_KMS_AUTH_TOKEN: "capability",
    },
    stdio: ["pipe", "pipe", "ignore"],
    detached: true,
  });
  assert.equal(f.calls[0].file, "/usr/bin/systemd-run");
  assert.equal(f.calls[0].args.includes("--expand-environment=no"), true);
  assert.equal(f.calls[0].args.includes("--setenv=WAYLAND_DISPLAY=wayland-2"), true);
  assert.equal(
    f.calls[0].args.some((value) => value.includes("LD_PRELOAD")),
    false,
  );
  assert.equal(f.calls[0].args.includes("--setenv=DPE_KMS_AUTH_SOCKET=/private/socket"), true);
  assert.equal(f.calls[0].args.includes("--setenv=DPE_KMS_AUTH_TOKEN=capability"), true);
  assert.equal(f.calls[0].args.at(-1), "literal$VALUE");
  child.stopHostCapture();
  child.stopHostCapture();
  assert.equal(f.calls.length, 2);
  assert.deepEqual(f.calls[1].args, [
    "--user",
    "--no-block",
    "stop",
    "dpe-capture-123-unique.service",
  ]);
});
test("unrestricted workers keep the existing process launch", () => {
  const f = fixture({ restricted: false });
  f.api.spawn("/app/dpe-kms-source", ["--list"], { env: {} });
  assert.equal(f.calls[0].file, "/app/dpe-kms-source");
});

test("permission service has its own lifetime bound if the calling app disappears", () => {
  const f = fixture();
  f.api.execFile(
    "/usr/bin/pkexec",
    ["--allow"],
    { env: { PATH: "/usr/bin" }, timeout: 120000 },
    () => {},
  );
  assert.equal(f.calls[0].args.includes("--property=RuntimeMaxSec=120"), true);
  assert.deepEqual(f.calls[0].args.slice(-3), ["--", "/usr/bin/pkexec", "--allow"]);
});
