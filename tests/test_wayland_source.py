import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_wayland_capture import protocols
from build_wayland_source import build
from common import ROOT


class WaylandSource(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("cc") and shutil.which("wayland-scanner"), "Wayland development tools required"
    )
    def test_build_ownership_and_capabilities_without_host_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build(root / "build")
            binary = root / "build/dpe-wayland-source"
            env = {**os.environ, "XDG_RUNTIME_DIR": str(root), "WAYLAND_DISPLAY": "absent"}
            for args in (
                [],
                ["--output", "", "--fps", "60"],
                ["--output", "DP-1", "--fps", "0"],
                ["--output", "DP-1", "--fps", "241"],
                ["--output", "DP-1", "--fps", "60x"],
                ["--output", "DP-1", "--fps", ""],
                ["--output", "DP-1", "--fps", "-1"],
                ["--output", "x" * 128, "--fps", "60"],
                ["--list", "--output", "DP-1"],
            ):
                self.assertEqual(subprocess.run([binary, *args], env=env, timeout=2).returncode, 2)
            result = subprocess.run(
                [binary, "--list"], env=env, capture_output=True, text=True, check=True, timeout=2
            )
            self.assertEqual(
                json.loads(result.stdout),
                dict(
                    version=1,
                    backend="wayland-direct",
                    available=False,
                    reason="wayland-unavailable",
                    outputs=[],
                ),
            )
            generated = protocols(root / "protocols")
            flags = subprocess.check_output(
                [
                    "pkg-config",
                    "--cflags",
                    "--libs",
                    "wayland-client",
                    "libpipewire-0.3",
                    "gbm",
                    "libdrm",
                ],
                text=True,
            ).split()
            check = root / "check"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-O1",
                    "-g",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer",
                    "-I" + str(root / "protocols"),
                    str(ROOT / "tests/screenshare/wayland-source-check.c"),
                    str(ROOT / "integration/video/wayland-capture.c"),
                    *map(str, generated),
                    *flags,
                    "-Wl,--wrap=pw_stream_return_buffer",
                    "-Wl,--wrap=pw_stream_queue_buffer",
                    "-Wl,--wrap=pw_stream_trigger_process",
                    "-Wl,--wrap=pw_stream_dequeue_buffer",
                    "-Wl,--wrap=dpe_wl_capture_submit",
                    "-o",
                    str(check),
                ],
                check=True,
            )
            result = subprocess.run([check], env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("ownership", result.stdout)


if __name__ == "__main__":
    unittest.main()
