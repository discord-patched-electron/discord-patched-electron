"""Exercise real SPA format handling and stream lifetime without a GPU."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_camera_support import build, node_headers
from capture_upstream_scope import scope
from common import ROOT


class CaptureUpstreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(
            ["cargo", "build", "--locked", "-p", "dpe-pipewire", "--example", "upstream-fixture"],
            cwd=ROOT,
            check=True,
        )

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
                    *flags,
                    str(ROOT / "tests/screenshare" / source),
                    str(ROOT / "out/rust-target/debug/examples/libupstream_fixture.a"),
                    "-ldrm",
                    "-lm",
                    "-ldl",
                    "-pthread",
                    "-o",
                    str(binary),
                ],
                check=True,
            )
            result = subprocess.run([str(binary)], capture_output=True, timeout=10, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unknown_native_module_keeps_upstream_behavior(self):
        with tempfile.TemporaryDirectory() as temporary:
            module = Path(temporary) / "unknown.node"
            module.write_bytes(b"unreviewed")
            self.assertEqual(scope(module)["reason"], "unreviewed-native-module")

    def test_only_reviewed_video_calls_are_redirected(self):
        flags = subprocess.check_output(
            ["pkg-config", "--cflags", "libpipewire-0.3"], text=True
        ).split()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            build(directory / "helper")
            subprocess.run(
                [
                    "cc",
                    "-std=gnu11",
                    "-O1",
                    "-fPIC",
                    "-shared",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-Wl,--no-relax",
                    "-I" + str(node_headers()),
                    *flags,
                    str(ROOT / "tests/screenshare/capture-upstream-fixture.c"),
                    "-ldl",
                    "-o",
                    str(directory / "discord_voice.node"),
                ],
                check=True,
            )
            for mode in ("0", "1"):
                result = subprocess.run(
                    [
                        "node",
                        str(ROOT / "tests/screenshare/capture-upstream-native-check.cjs"),
                        str(directory),
                        str(directory / "helper/libdpe-camera-probe.so"),
                        mode,
                    ],
                    capture_output=True,
                    timeout=10,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
