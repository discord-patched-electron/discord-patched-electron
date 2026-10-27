const assert = require("node:assert/strict");
const { test } = require("node:test");
const relaunch = require("../../integration/updater/relaunch.cjs");

test("relaunch keeps original user arguments rather than resolved preset switches", () => {
  const args = ["--flag=one=two", "discord://example", "argument with spaces"];
  assert.deepEqual(relaunch.arguments({ DPE_LAUNCH_ARGS: JSON.stringify(args) }), args);
  for (const value of ["oops", "{}", '["ok",2]', "null", ""])
    assert.deepEqual(relaunch.arguments({ DPE_LAUNCH_ARGS: value }), []);
});

test("normal restart exits recovery; ordinary restart preserves it", (t) => {
  const previous = { ...process.env };
  t.after(() => {
    process.env = previous;
  });
  process.env.DPE_LAUNCHER = "/test/launch.sh";
  process.env.DPE_LAUNCH_ARGS = '["--ignore-flags"]';
  process.env.DPE_IGNORE_FLAGS = "1";
  let last,
    quits = 0;
  const app = {
    relaunch: (options) => {
      last = options;
    },
    quit: () => {
      quits++;
    },
  };
  relaunch.restart(app);
  assert.deepEqual(last, { execPath: "/test/launch.sh", args: ["--ignore-flags"] });
  assert.equal(process.env.DPE_IGNORE_FLAGS, "1");
  relaunch.restart(app, { normal: true });
  assert.deepEqual(last, { execPath: "/test/launch.sh", args: [] });
  assert.equal(process.env.DPE_IGNORE_FLAGS, undefined);
  assert.equal(quits, 2);
});
