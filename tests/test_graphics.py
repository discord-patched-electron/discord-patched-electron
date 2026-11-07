"""Check Rust graphics ABI rejection without opening a GPU."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_rust_support import build as build_rust
from common import ROOT


class Graphics(unittest.TestCase):
    def test_stale_handles_and_thread_confined_abi(self):
        self.fixture("kms-gpu-check.c")

    def test_game_gpu_stale_handles_and_callbacks(self):
        self.fixture("vkcapture-gpu-check.c")

    def fixture(self, source):
        archive, _ = build_rust(
            "dpe-graphics", features=("game",) if source == "vkcapture-gpu-check.c" else ()
        )
        flags = subprocess.check_output(
            ["pkg-config", "--cflags", "libdrm", "libpipewire-0.3"], text=True
        ).split()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "graphics-check"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    *flags,
                    str(ROOT / "tests/screenshare" / source),
                    str(archive),
                    "-Wl,--gc-sections",
                    *(
                        ["-Wl,--wrap=glCheckFramebufferStatus"]
                        if source == "kms-gpu-check.c"
                        else []
                    ),
                    "-lEGL",
                    "-lGLESv2",
                    "-lgbm",
                    "-ldl",
                    "-lpthread",
                    "-lm",
                    "-o",
                    str(target),
                ],
                check=True,
            )
            result = subprocess.run([target], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("without GPU access", result.stdout)
