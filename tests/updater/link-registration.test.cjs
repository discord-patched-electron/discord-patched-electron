const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { test } = require("node:test");
const { createRegistration } = require("../../integration/desktop/link-registration.cjs");

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dpe-links-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const data = path.join(root, "data");
  const applications = path.join(data, "applications");
  fs.mkdirSync(applications, { recursive: true });
  const launcher = path.join(root, "launch.sh");
  fs.writeFileSync(launcher, "#!/bin/sh\n");
  let handler = "discord.desktop";
  let fail = false;
  const env = {
    HOME: root,
    XDG_DATA_HOME: data,
    XDG_DATA_DIRS: path.join(root, "system"),
    DPE_LAUNCHER: launcher,
  };
  const run = (_program, args) => {
    if (args[0] === "query") return handler;
    if (!fail) handler = args[1];
    return "";
  };
  return {
    env,
    applications,
    launcher,
    registration: () => createRegistration({ env, run }),
    entry: (exec) =>
      fs.writeFileSync(
        path.join(applications, "discord.desktop"),
        `[Desktop Entry]\nType=Application\nExec=${exec}\n`,
      ),
    select: (value) => (handler = value),
    fail: () => (fail = true),
    handler: () => handler,
  };
}

test("the same Discord desktop ID pointing at another build is not this app", (t) => {
  const f = fixture(t);
  f.entry("/usr/bin/other-discord %U");
  const r = f.registration();
  assert.equal(r.current(), false);
  assert.equal(r.elsewhere(), true);
  r.claim();
  assert.equal(r.current(), true);
  assert.match(f.handler(), /^dpe-links-/);
  f.select("discord.desktop");
  assert.equal(r.current(), false);
});

test("installed launcher symlinks are accepted and shadowed entries are checked", (t) => {
  const f = fixture(t);
  const alias = path.join(path.dirname(f.launcher), "installed");
  fs.symlinkSync(f.launcher, alias);
  f.entry(`${alias} %U`);
  assert.equal(f.registration().current(), true);
  f.entry(`${alias} --other-profile %U`);
  assert.equal(f.registration().current(), false);
  f.entry(`${alias} %U\nDBusActivatable=true`);
  assert.equal(f.registration().current(), false);
  f.entry(`${alias} %U\nHidden=true`);
  assert.equal(f.registration().current(), false);
});

test("development commands distinguish versions and profiles and never register the signal wrapper", (t) => {
  const f = fixture(t);
  f.env.DPE_LINK_COMMAND = JSON.stringify([
    f.launcher,
    "--home",
    "/dev home",
    "open",
    "current",
    "--profile",
    "dev",
    "--",
  ]);
  const r = f.registration();
  r.claim();
  const first = f.handler();
  assert.equal(r.current(), true);
  f.env.DPE_LINK_COMMAND = JSON.stringify([f.launcher, "--profile", "other"]);
  const other = f.registration();
  assert.equal(other.current(), false);
  other.claim();
  assert.notEqual(f.handler(), first);
  assert.equal(r.current(), false);
});

test("claiming verifies the actual association and rejects unknown launchers", (t) => {
  const f = fixture(t);
  f.fail();
  assert.throws(() => f.registration().claim(), /did not take effect/);
  f.env.DPE_LINK_COMMAND = "invalid json";
  assert.equal(f.registration().current(), false);
  assert.throws(() => f.registration().claim(), /determine this app/);
});

test(
  "GIO launches generated entries with escaped paths, arguments and URLs",
  {
    skip: !fs.existsSync("/usr/bin/gio"),
  },
  async (t) => {
    const f = fixture(t);
    const launcher = path.join(path.dirname(f.launcher), 'launch "$%`\\.sh');
    const log = path.join(path.dirname(f.launcher), "opened.txt");
    fs.writeFileSync(launcher, '#!/bin/sh\noutput=$1\nshift\nprintf "%s\\n" "$@" > "$output"\n', {
      mode: 0o755,
    });
    const argument = 'spaces " quotes $ % ` \\';
    f.env.DPE_LINK_COMMAND = JSON.stringify([launcher, log, argument]);
    f.registration().claim();
    require("node:child_process").execFileSync("/usr/bin/gio", [
      "launch",
      path.join(f.applications, f.handler()),
      "discord://invite/test",
    ]);
    for (let attempt = 0; attempt < 100 && !fs.existsSync(log); attempt++)
      await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(fs.readFileSync(log, "utf8"), argument + "\ndiscord://invite/test\n");
  },
);
