"""Cursor-only metadata bounds and GPU shader fixture; no desktop capture."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GameCursor(unittest.TestCase):
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
            ],
            text=True,
        ).split()
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "cursor-check"
            subprocess.run(
                [
                    "cc",
                    "-std=gnu11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    str(ROOT / "tests/screenshare/vkcapture-cursor-check.c"),
                    *flags,
                    "-o",
                    str(binary),
                ],
                check=True,
            )
            subprocess.run([str(binary)], check=True, timeout=5)
