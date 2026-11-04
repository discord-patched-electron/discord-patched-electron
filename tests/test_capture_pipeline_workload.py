"""Compile the owned GL workload and reject unsafe arguments without a display."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CapturePipelineWorkloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("cc") or not shutil.which("pkg-config"):
            raise unittest.SkipTest("C compiler and pkg-config required")
        flags = subprocess.run(
            ["pkg-config", "--cflags", "--libs", "sdl3", "gl"],
            capture_output=True,
            text=True,
            check=False,
        )
        if flags.returncode:
            raise unittest.SkipTest("SDL3 and desktop OpenGL development files required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="dpe-pipeline-workload-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.binary = Path(cls.temporary.name) / "source"
        subprocess.run(
            [
                "cc",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(ROOT / "tests/screenshare/pipeline-workload.c"),
                "-o",
                str(cls.binary),
                *flags.stdout.split(),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

    def test_invalid_arguments_reject_before_display_or_gpu_initialization(self):
        invalid = [
            [],
            ["0", "240"],
            ["91", "240"],
            ["-1", "uncapped"],
            ["5x", "240"],
            ["5", "241"],
            ["5", "4001"],
            ["5", "90"],
            ["5", "120x"],
            ["5", "240", "79", "720"],
            ["5", "240", "1280", "63"],
            ["5", "uncapped", "4097", "720"],
            ["5", "240", "1280", "720", "unknown"],
            ["5", "240", "1280", "720", "tiles", "--fullscreen", "extra"],
        ]
        for arguments in invalid:
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [str(self.binary), *arguments],
                    env={**os.environ, "SDL_VIDEODRIVER": "dpe-no-display-driver"},
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("usage:", result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("SDL:", result.stderr)

    def test_valid_modes_reach_only_the_explicitly_disabled_display(self):
        for arguments in (
            ["1", "60", "1280", "720", "tiles", "--fullscreen"],
            ["1", "120", "1280", "720", "tiles", "--fullscreen"],
            ["1", "240", "1280", "720", "tiles", "--fullscreen"],
            ["1", "600", "1280", "720", "tiles", "--fullscreen"],
            ["1", "2000", "1280", "720", "tiles", "--fullscreen"],
            ["1", "4000", "1280", "720", "tiles", "--fullscreen"],
            ["90", "uncapped", "1920", "1080", "scroll"],
        ):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [str(self.binary), *arguments],
                    env={**os.environ, "SDL_VIDEODRIVER": "dpe-no-display-driver"},
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertIn("SDL:", result.stderr)
                self.assertEqual(result.stdout, "")
