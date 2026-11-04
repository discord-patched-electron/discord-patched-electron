"""Exercise real SPA format handling and stream lifetime without a GPU."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_camera_support import node_headers
from capture_upstream_scope import scope
from common import ROOT


class CaptureUpstreamTests(unittest.TestCase):
    def test_producer_capability_rates_and_reentrant_lifetime(self):
        self.run_fixture("capture-upstream-check.c")

    def test_initial_preference_preserves_spa_compatibility(self):
        self.run_fixture("capture-upstream-initial-check.c")

    def run_fixture(self, source):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "upstream-check"
            flags = subprocess.check_output(
                ["pkg-config", "--cflags", "libpipewire-0.3"], text=True
            ).split()
            subprocess.run(
                [
                    "cc",
                    "-std=gnu11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I" + str(node_headers()),
                    *flags,
                    str(ROOT / "tests/screenshare" / source),
                    "-ldl",
                    "-pthread",
                    "-o",
                    str(binary),
                ],
                check=True,
            )
            subprocess.run([str(binary)], check=True, capture_output=True)

    def test_unknown_native_module_keeps_upstream_behavior(self):
        with tempfile.TemporaryDirectory() as temporary:
            module = Path(temporary) / "unknown.node"
            module.write_bytes(b"unreviewed")
            self.assertEqual(scope(module)["reason"], "unreviewed-native-module")


if __name__ == "__main__":
    unittest.main()
