"""Exercise the real preload boundary with a fake V4L2 driver; no camera needed."""

import os
import resource
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_camera_support import build
from camera_scope import scope
from common import ROOT


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
        self.run_probe(DPE_CAMERA_PROBE_START="1", DPE_CAMERA_PROBE_END="2", DPE_TEST_BYPASS="1")

    def test_unknown_voice_layout_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown camera"):
            scope(self.root / "driver.so")

    def test_launcher_keeps_camera_probe_active_after_relocation_to_a_path_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="camera launcher ") as temporary:
            app = Path(temporary)
            shutil.copytree(ROOT / "integration/launcher", app / "integration/launcher")
            voice = app / "modules/discord_voice"
            voice.mkdir(parents=True)
            (voice / "discord_voice.node").symlink_to(self.root / "discord_voice.node")
            (app / "runtime").mkdir()
            (app / "runtime/electron").symlink_to(self.root / "check")
            (app / "libdpe-camera-probe.so").symlink_to(self.root / "libdpe-camera-probe.so")
            (app / "camera-probe.conf").write_text(f"{self.probe['start']} {self.probe['end']}\n")
            launcher = app / "launch.sh"
            launcher.write_text(
                (ROOT / "integration/launcher/discord-launcher.sh")
                .read_text()
                .replace("root=/usr/lib/@PKGNAME@", "root=" + shlex.quote(str(app)))
                .replace("@PKGNAME@", "dpe-camera-fixture")
                .replace("@DESKTOP_ID@", "dpe-camera-fixture")
                .replace("@PROFILE_NAME@", "test-profile")
            )
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("DPE_", "LD_", "DISCORD_"))
            }
            result = subprocess.run(
                ["bash", str(launcher), "--ignore-flags"],
                env={
                    **environment,
                    "XDG_CONFIG_HOME": str(app / "config"),
                    "LD_PRELOAD": str(self.root / "driver.so"),
                },
                preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)),
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
