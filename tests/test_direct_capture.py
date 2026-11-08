import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_rust_support import build as build_rust


class DirectCapture(unittest.TestCase):
    def test_descriptor_transport_bounds_and_ownership(self):
        self.fixture("direct-wire-check.c", "ownership, bounds and EOF passed")

    def test_worker_rates_loss_and_cleanup(self):
        self.fixture("direct-worker-check.c", "pause/rate/source-loss/cleanup passed")

    def test_encoder_import_cache_identity_and_retained_leases(self):
        self.fixture("encoder-import-cache-check.c", "retained leases passed")

    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def fixture(self, source, expected):
        root = Path(__file__).resolve().parents[1]
        archive, _ = build_rust(
            "dpe-native" if source == "direct-worker-check.c" else "dpe-os", capture_gpu=False
        )
        extra = [str(archive), "-ldl", "-lm", "-lva", "-lva-drm", "-Wl,--gc-sections"]
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "check"
            subprocess.run(
                [
                    "cc",
                    "-O1",
                    "-g",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-fsanitize=address,undefined",
                    str(root / "tests/screenshare" / source),
                    *extra,
                    "-pthread",
                    "-o",
                    str(binary),
                ],
                check=True,
            )
            result = subprocess.run([binary], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(expected, result.stdout)
