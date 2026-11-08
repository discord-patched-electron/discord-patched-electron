const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { test } = require("node:test");

test("persistent runtime diagnostics preserve streams and record renderer and utility failures", () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "dpe-runtime-log-"));
  try {
    const script = `
      const {EventEmitter} = require('node:events');
      const app = new EventEmitter();
      const logger = require(${JSON.stringify(path.resolve(__dirname, "../../integration/updater/diagnostic-log.cjs"))});
      logger.install(app);
      process.stdout.write('protocol-'); process.stdout.write('unchanged\\n');
      const helper = new (require("node:stream").PassThrough)();
      logger.capture(helper, "helper-stderr"); helper.write("fragmented-"); helper.end("error\\n");
      const content = new EventEmitter(); content.id = 1; content.getURL = () => 'https://canary.discord.com/channels/@me';
      app.emit('web-contents-created', {}, content);
      content.emit('console-message', {level:'error', message:'Synthetic renderer failure'});
      content.emit('render-process-gone', {}, {reason:'crashed', exitCode:139});
      app.emit('child-process-gone', {}, {type:'GPU', reason:'crashed'});
      content.getURL = () => 'https://untrusted.invalid/';
      content.emit('console-message', {message:'DO_NOT_RECORD'});
    `;
    assert.equal(
      execFileSync(process.execPath, ["-e", script], {
        env: { ...process.env, DPE_PROFILE_DIR: profile },
        encoding: "utf8",
      }),
      "protocol-unchanged\n",
    );
    const file = path.join(profile, "logs/dpe/runtime.log");
    const records = fs.readFileSync(file, "utf8").trim().split("\n").map(JSON.parse);
    for (const event of [
      "process-start",
      "stdout",
      "renderer-console",
      "renderer-gone",
      "child-process-gone",
      "process-exit",
    ])
      assert.ok(
        records.some((entry) => entry.event === event),
        event,
      );
    assert.ok(
      records.some(
        (entry) => entry.event === "helper-stderr" && entry.details.message === "fragmented-error",
      ),
    );
    assert.ok(!fs.readFileSync(file, "utf8").includes("DO_NOT_RECORD"));
    assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  } finally {
    fs.rmSync(profile, { recursive: true, force: true });
  }
});

test("recording to an unavailable destination does not throw", () => {
  const { record } = require("../../integration/updater/diagnostic-log.cjs");
  assert.doesNotThrow(() => record("test", {}, "/dev/null/not-a-directory"));
});
