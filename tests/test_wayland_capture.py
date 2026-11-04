import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_wayland_capture import build, protocols
from common import ROOT


class WaylandCapture(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("cc") and shutil.which("wayland-scanner"), "Wayland development tools required"
    )
    def test_protocol_adapter_without_host_compositor_or_gpu(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            build(output / "shared")
            generated = protocols(output / "protocols")
            flags = subprocess.check_output(
                ["pkg-config", "--cflags", "--libs", "wayland-client", "wayland-server"], text=True
            ).split()
            binary = output / "check"
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
                    "-I" + str(output / "protocols"),
                    str(ROOT / "tests/screenshare/wayland-check.c"),
                    str(ROOT / "integration/video/wayland-capture.c"),
                    *map(str, generated),
                    *flags,
                    "-o",
                    str(binary),
                ],
                check=True,
            )
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("bounded requests", result.stdout)


if __name__ == "__main__":
    unittest.main()
