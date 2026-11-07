const { test } = require("node:test");
const assert = require("node:assert/strict");
const { systemTool } = require("../../integration/audio/system-tools.cjs");

test("AppImage uses bundled Python and host audio/desktop tools", () => {
  const previous = process.env.DPE_TOOLS_DIR;
  try {
    delete process.env.DPE_TOOLS_DIR;
    assert.equal(systemTool("python3"), "/usr/bin/python3");
    process.env.DPE_TOOLS_DIR = "/tmp/AppImage with spaces/usr/bin";
    assert.equal(systemTool("python3"), "/tmp/AppImage with spaces/usr/bin/python3");
    for (const name of ["pactl", "xprop", "pw-dump", "pw-cli", "pw-cat", "pw-loopback"])
      assert.equal(systemTool(name), `/usr/bin/${name}`);
  } finally {
    if (previous === undefined) delete process.env.DPE_TOOLS_DIR;
    else process.env.DPE_TOOLS_DIR = previous;
  }
});
