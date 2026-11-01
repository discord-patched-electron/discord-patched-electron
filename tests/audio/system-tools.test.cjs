const { test } = require("node:test");
const assert = require("node:assert/strict");
const { systemTool } = require("../../integration/audio/system-tools.cjs");

test("native helpers retain system paths and AppImage helpers follow the bundled directory", () => {
  const previous = process.env.DPE_TOOLS_DIR;
  try {
    delete process.env.DPE_TOOLS_DIR;
    assert.equal(systemTool("python3"), "/usr/bin/python3");
    process.env.DPE_TOOLS_DIR = "/tmp/AppImage with spaces/usr/bin";
    for (const name of ["python3", "pactl", "xprop"])
      assert.equal(systemTool(name), `/tmp/AppImage with spaces/usr/bin/${name}`);
  } finally {
    if (previous === undefined) delete process.env.DPE_TOOLS_DIR;
    else process.env.DPE_TOOLS_DIR = previous;
  }
});
