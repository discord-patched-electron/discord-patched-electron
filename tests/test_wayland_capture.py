import os
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
    def test_owned_rust_capture_against_private_wire_server(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            generated = protocols(output / "protocols")
            binary = output / "server"
            flags = subprocess.check_output(
                ["pkg-config", "--cflags", "--libs", "wayland-client", "wayland-server"], text=True
            ).split()
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
                    "-DDPE_WAYLAND_SERVER_ONLY",
                    "-I" + str(output / "protocols"),
                    str(ROOT / "tests/screenshare/wayland-check.c"),
                    *map(str, generated),
                    *flags,
                    "-o",
                    str(binary),
                ],
                check=True,
            )
            result = subprocess.run(
                [
                    "cargo",
                    "test",
                    "--offline",
                    "--locked",
                    "-p",
                    "dpe-wayland",
                    "--test",
                    "output",
                    "--",
                    "--ignored",
                    "--nocapture",
                ],
                cwd=ROOT,
                env={**os.environ, "DPE_WAYLAND_TEST_SERVER": str(binary)},
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("lease teardown passed", result.stdout)

    @unittest.skipUnless(
        shutil.which("cc") and shutil.which("wayland-scanner"), "Wayland development tools required"
    )
    def test_protocol_adapter_without_host_compositor_or_gpu(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            build(output / "shared")
            self.assertTrue((output / "shared/libdpe-wayland.a").is_file())


if __name__ == "__main__":
    unittest.main()
