"""Bounded AV1 crop metadata, shared by the sender and Electron decoder."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("cc") and shutil.which("c++"), "C/C++ compilers required")
class Av1Crop(unittest.TestCase):
    def test_software_decoder_views_reordering_retry_and_lifecycle(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "software-crop-check"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-fsanitize=undefined",
                    str(ROOT / "tests/screenshare/av1-software-crop-check.c"),
                    "-ldl",
                    "-pthread",
                    "-o",
                    str(binary),
                ],
                check=True,
                capture_output=True,
            )
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertIn("isolation and teardown passed", result.stdout)

    def test_metadata_bounds_keyframe_reset_and_malformed_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "crop-check"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-fsanitize=undefined",
                    str(ROOT / "tests/screenshare/av1-crop-check.c"),
                    "-o",
                    str(binary),
                ],
                check=True,
                capture_output=True,
            )
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertIn("randomized inputs passed", result.stdout)
            subprocess.run(
                [
                    "c++",
                    "-std=c++20",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-pedantic",
                    "-x",
                    "c++",
                    "-c",
                    str(ROOT / "integration/video/av1-crop.h"),
                    "-o",
                    str(Path(temporary) / "crop.o"),
                ],
                check=True,
                capture_output=True,
            )

    def test_runtime_patch_carries_the_same_parser(self):
        patch = (ROOT / "patches/ports/46/av1-crop-metadata.patch").read_text()
        begin = patch.index("+++ b/discord/av1_crop.h\n")
        lines = patch[begin:].splitlines()
        added = (
            "\n".join(
                line[1:] for line in lines if line.startswith("+") and not line.startswith("+++")
            )
            + "\n"
        )
        self.assertEqual(added, (ROOT / "integration/video/av1-crop.h").read_text())
