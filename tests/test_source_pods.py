"""Check Rust source negotiation against PipeWire's real SPA parser."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_rust_support import build
from common import ROOT


class SourcePods(unittest.TestCase):
    def test_real_spa_formats_buffer_contracts_and_reentrant_ownership(self):
        rust, _ = build("dpe-pipewire")
        flags = subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "libpipewire-0.3"], text=True
        ).split()
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "source-pods-check"
            subprocess.run(
                [
                    "cc",
                    "-std=gnu11",
                    "-O1",
                    "-g",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer",
                    str(ROOT / "tests/screenshare/source-pods-check.c"),
                    str(rust),
                    "-Wl,--gc-sections",
                    "-ldrm",
                    "-ldl",
                    "-pthread",
                    "-lm",
                    *flags,
                    "-o",
                    str(binary),
                ],
                check=True,
            )
            result = subprocess.run([binary], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("real SPA parsing", result.stdout)
