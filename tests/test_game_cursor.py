"""Cursor-only metadata bounds and GPU shader fixture; no desktop capture."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_rust_support import build as build_rust
from build_wayland_capture import protocols

ROOT = Path(__file__).resolve().parents[1]


class GameCursor(unittest.TestCase):
    @unittest.skipUnless(shutil.which("Xvfb") and shutil.which("cc"), "Xvfb and C compiler")
    def test_x11_shape_geometry_events_and_bounded_position_polling(self):
        rust, _ = build_rust("dpe-x11")
        flags = subprocess.check_output(
            ["pkg-config", "--cflags", "--libs", "xcb", "libspa-0.2"], text=True
        ).split()
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "x11-cursor"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(ROOT / "tests/screenshare/x11-cursor-check.c"),
                    str(rust),
                    "-Wl,--gc-sections",
                    "-ldl",
                    "-lpthread",
                    "-lm",
                    *flags,
                    "-o",
                    str(binary),
                ],
                check=True,
            )
            with subprocess.Popen(
                ["Xvfb", "-displayfd", "1", "-screen", "0", "640x480x24", "-nolisten", "tcp"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            ) as server:
                try:
                    display = server.stdout.readline().strip()
                    self.assertTrue(display.isdecimal(), "Xvfb did not allocate a private display")
                    subprocess.run(
                        [str(binary)],
                        env={**os.environ, "DISPLAY": f":{display}"},
                        check=True,
                        timeout=10,
                    )
                finally:
                    server.terminate()
                    server.wait(timeout=5)

    @unittest.skipUnless(
        shutil.which("cc") and shutil.which("pkg-config"), "native development tools"
    )
    def test_cursor_geometry_hidden_and_payload_bounds(self):
        flags = subprocess.check_output(
            [
                "pkg-config",
                "--cflags",
                "--libs",
                "libpipewire-0.3",
                "gbm",
                "egl",
                "glesv2",
                "libdrm",
                "wayland-client",
                "wayland-server",
            ],
            text=True,
        ).split()
        rust, _ = build_rust("dpe-wayland")
        with tempfile.TemporaryDirectory() as directory:
            generated = protocols(Path(directory) / "protocols")
            wire = Path(directory) / "cursor-wire"
            subprocess.run(
                [
                    "cc",
                    "-std=gnu11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(ROOT / "tests/screenshare/vkcapture-cursor-wire-check.c"),
                    str(rust),
                    "-ldl",
                    "-lpthread",
                    "-lm",
                    "-I" + str(Path(directory) / "protocols"),
                    *map(str, generated),
                    *flags,
                    "-o",
                    str(wire),
                ],
                check=True,
            )
            subprocess.run([str(wire)], check=True, timeout=10)
