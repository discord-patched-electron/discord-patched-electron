"""Load a real ELF and redirect its reviewed calls through the Rust addon."""

import subprocess
import tempfile
from pathlib import Path

from build_camera_support import build, node_headers
from common import ROOT


def check_fixture(kind):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        module = root / "native-fixture.node"
        subprocess.run(
            [
                "cc",
                "-std=gnu11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-fsanitize=undefined",
                "-fPIC",
                "-shared",
                "-Wl,-z,relro,-z,now",
                "-I" + str(node_headers()),
                str(ROOT / f"tests/screenshare/capture-{kind}-check.c"),
                "-ldl",
                "-lm",
                "-o",
                str(module),
            ],
            check=True,
        )
        build(root / "addon")
        result = subprocess.run(
            [
                "node",
                "-e",
                """
const assert = require('node:assert/strict');
const [filename, helper, kind] = process.argv.slice(1);
const fixture = require(filename);
const addon = {exports: {}};
process.dlopen(addon, helper);
const role = kind === 'scale' ? 'Scale' : 'Sync';
const install = addon.exports[`installCapture${role}`];
const stats = () => JSON.parse(addon.exports[`capture${role}Stats`]());
assert.equal(stats().installed, false);
assert.throws(() => install(filename, fixture.site + 1, fixture.target), /context/);
assert.throws(() => install(filename, fixture.site, fixture.target + 1), /call differs/);
assert.throws(() => install(filename, 0, fixture.target), /offset/);
assert.throws(() => install(filename + '.missing', fixture.site, fixture.target), /not loaded/);
assert.throws(() => install(filename, 0xfffffff0, fixture.target), /context/);
assert.equal(stats().installed, false);
install(filename, fixture.site, fixture.target);
install(filename, fixture.site, fixture.target);
assert.throws(() => install(filename, fixture.site + 1, fixture.target), /module changed/);
fixture.run();
assert.equal(stats().installed, true);
assert.ok(stats().calls > 0);
if (kind === 'scale') {
  assert.ok(stats().limited > 0);
  assert.ok(stats().invalidWants > 0);
} else {
  assert.equal(stats().correctedWaits, 1);
  assert.equal(stats().waitErrors, 4);
  assert.equal(stats().clockFailures, 0);
}
console.log('Rust adapter and real native call dispatch passed');
""",
                str(module),
                str(root / "addon/libdpe-camera-probe.so"),
                kind,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.stdout
