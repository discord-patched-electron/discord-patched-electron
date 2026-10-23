"""Exercise the real preload boundary with a fake V4L2 driver; no camera needed."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import ROOT
from camera_scope import scope
from build_camera_support import build


class CameraProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        build(cls.root)
        for source, destination in [
            ("native.c", "discord_voice.node"),
            ("driver.c", "driver.so"),
        ]:
            subprocess.run(
                [
                    "cc",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-fPIC",
                    "-shared",
                    "-fno-optimize-sibling-calls",
                    str(ROOT / "tests/camera" / source),
                    "-o",
                    str(cls.root / destination),
                ],
                check=True,
            )
        subprocess.run(
            [
                "cc",
                "-Wall",
                "-Wextra",
                "-Werror",
                str(ROOT / "tests/camera/check.c"),
                "-o",
                str(cls.root / "check"),
                "-ldl",
            ],
            check=True,
        )
        cls.probe = scope(cls.root / "discord_voice.node")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_probe(self, **env):
        subprocess.run(
            [str(self.root / "check")],
            env={
                **os.environ,
                "LD_PRELOAD": str(self.root / "libdpe-camera-probe.so")
                + ":"
                + str(self.root / "driver.so"),
                "DPE_CAMERA_VOICE_MODULE": str(self.root / "discord_voice.node"),
                "DPE_CAMERA_FAST_PROBE": "1",
                "DPE_CAMERA_PROBE_START": str(self.probe["start"]),
                "DPE_CAMERA_PROBE_END": str(self.probe["end"]),
                **env,
            },
            check=True,
        )

    def test_only_the_identified_probe_skips_unsupported_formats(self):
        self.run_probe()

    def test_disable_and_invalid_function_scope_preserve_the_driver(self):
        self.run_probe(DPE_CAMERA_FAST_PROBE="0", DPE_TEST_BYPASS="1")
        self.run_probe(DPE_CAMERA_PROBE_START="invalid", DPE_TEST_BYPASS="1")
        self.run_probe(
            DPE_CAMERA_PROBE_START="1", DPE_CAMERA_PROBE_END="2", DPE_TEST_BYPASS="1"
        )

    def test_unknown_voice_layout_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown camera"):
            scope(self.root / "driver.so")
