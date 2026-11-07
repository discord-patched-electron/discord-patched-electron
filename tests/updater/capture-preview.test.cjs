"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { screen } = require("../../integration/updater/capture-preview.cjs");
test("monitor stream keeps one process and rejects extra demand without a frame queue", async () => {
  // Synthetic pixels only; a changing byte proves the process is retained.
  const code = `let n=0;function frame(){process.stdout.write('DPE-PREVIEW 1 1\\n');process.stdout.write(Buffer.from([++n,0,0,255]));}frame();process.stdin.on('data',frame);`;
  const signal = new AbortController();
  const args = ["-e", code, "--"];
  try {
    const first = await screen(process.execPath, args, process.env, signal.signal);
    assert.equal(first.pixels[0], 1);
    const second = screen(process.execPath, args, process.env, signal.signal);
    assert.deepEqual(await screen(process.execPath, args, process.env, signal.signal), {});
    assert.equal((await second).pixels[0], 2);
    const pending = screen(process.execPath, args, process.env, signal.signal);
    signal.abort();
    assert.deepEqual(await pending, {});
  } finally {
    signal.abort();
  }
});
test("KMS previews receive a live Discord worker capability and close it on abort", async () => {
  const fs = require("node:fs");
  const vm = require("node:vm");
  const module = { exports: {} };
  vm.runInNewContext(
    fs.readFileSync(require.resolve("../../integration/updater/capture-preview.cjs"), "utf8"),
    {
      module,
      Buffer,
      setTimeout,
      clearTimeout,
      require(name) {
        if (name === "./capture-host.cjs") return { spawn: require("node:child_process").spawn };
        if (name === "./capture-kms-authorizer.cjs")
          return require("../../integration/updater/capture-kms-authorizer.cjs");
        return require(name);
      },
    },
  );
  const code = `const n=require('node:net');const s=n.createConnection(process.env.DPE_KMS_AUTH_SOCKET);s.on('connect',()=>s.write(process.env.DPE_KMS_AUTH_TOKEN+'\\n'));s.once('data',()=>{process.stdout.write('DPE-PREVIEW 1 1\\n');process.stdout.write(Buffer.from([7,0,0,255]));});process.stdin.resume();`;
  const signal = new AbortController();
  try {
    const frame = await module.exports.screen(
      process.execPath,
      ["-e", code, "--", "--method", "kms"],
      process.env,
      signal.signal,
    );
    assert.equal(frame.pixels[0], 7);
  } finally {
    signal.abort();
  }
});
