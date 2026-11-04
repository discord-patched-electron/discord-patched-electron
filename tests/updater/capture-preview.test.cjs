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
