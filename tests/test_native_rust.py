"""Exercise Rust/C/Node boundaries without accounts, GPU access or native calls."""

import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_camera_support import build
from build_capture_fixture import build as build_delay_fixture
from build_rust_support import build as build_rust
from common import ROOT, sha256


class NativeRust(unittest.TestCase):
    def test_capture_delay_exists_only_in_the_disposable_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "delay.so"
            identity = build_delay_fixture(target)
            self.assertEqual(identity["features"], ["capture-delay-fixture"])
            result = subprocess.run(
                [
                    "node",
                    "-e",
                    """
const assert = require('node:assert/strict');
const addon = {exports: {}};
process.dlopen(addon, process.argv[1]);
for (const value of [-1, 201, 1.5, NaN, Infinity, '30'])
  assert.throws(() => addon.exports.testCaptureDelay(value), /integer/);
for (const value of [0, 1, 200]) addon.exports.testCaptureDelay(value);
assert.equal(JSON.parse(addon.exports.captureStats()).installed, false);
console.log('Bounded disposable capture delay passed');
""",
                    str(target),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("disposable capture delay passed", result.stdout)

    def test_runtime_dependency_notices_match_registry_checksums_and_retained_files(self):
        notices = ROOT / "native/licenses"
        manifest = json.loads((notices / "wayland-dependencies.json").read_text())
        lock = {
            (package["name"], package["version"]): package
            for package in tomllib.loads((ROOT / "Cargo.lock").read_text())["package"]
        }
        self.assertTrue(manifest["packages"])
        for package in manifest["packages"]:
            entry = lock[(package["name"], package["version"])]
            self.assertEqual(package["archive_sha256"], entry["checksum"])
            self.assertEqual(
                package["source"],
                f"https://crates.io/crates/{package['name']}/{package['version']}",
            )
        for relative, checksum in manifest["files"].items():
            self.assertEqual(sha256(notices / relative), checksum)

    def test_retained_archive_survives_another_feature_build(self):
        first, first_identity = build_rust(capture_gpu=False)
        original = sha256(first)
        second, second_identity = build_rust(capture_gpu=True)
        self.assertEqual(original, first_identity["sha256"])
        self.assertEqual(sha256(first), original)
        self.assertEqual(sha256(second), second_identity["sha256"])
        self.assertNotEqual(first, second)
        self.assertNotEqual(first_identity["sha256"], second_identity["sha256"])

    def test_repeated_addon_load_registers_cleanup_once_per_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            build(Path(directory))
            result = subprocess.run(
                [
                    "node",
                    "--input-type=module",
                    "-e",
                    """
for (let i = 0; i < 8; i++) {
  const addon = {exports: {}};
  process.dlopen(addon, process.argv[1]);
  if (typeof addon.exports.installCapturePreview !== 'function') process.exit(2);
  if ('testCaptureDelay' in addon.exports) process.exit(3);
}
console.log('Repeated module registration and environment teardown passed');
""",
                    str(Path(directory) / "libdpe-camera-probe.so"),
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("environment teardown passed", result.stdout)
