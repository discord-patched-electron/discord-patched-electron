import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Launcher(unittest.TestCase):
    def launch(self, config="", legacy="", args=(), env=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "runtime").mkdir()
            runtime = root / "runtime/electron"
            runtime.write_text(
                "#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n"
            )
            runtime.chmod(0o755)
            name = "discord-canary-prerelease-patched-electron-bin"
            (root / f"{name}-flags.conf").write_text(legacy)
            (root / "discord-canary-patched-electron.conf").write_text(config)
            launcher = root / "launch.sh"
            launcher.write_text(
                (ROOT / "integration/launcher/discord-launcher.sh")
                .read_text()
                .replace("@PKGNAME@", name)
                .replace("@DESKTOP_ID@", "io.github.discord_patched_electron.DiscordCanary")
                .replace("root=/usr/lib/" + name, "root=" + temp)
            )
            result = subprocess.check_output(
                ["bash", str(launcher), *args],
                text=True,
                env={
                    **os.environ,
                    "XDG_CONFIG_HOME": temp,
                    "DPE_CAMERA_FAST_PROBE": "0",
                    "WAYLAND_DISPLAY": "wayland-test",
                    **(env or {}),
                },
            )
            return json.loads(result)[1:]

    def test_defaults_and_feature_precedence(self):
        flags = self.launch(
            "--disable-features=AcceleratedVideoEncoder\n--enable-features=Extra\n--ozone-platform=x11\n",
            legacy="--enable-features=Legacy\n",
            args=["--enable-features=AcceleratedVideoEncoder"],
        )
        enabled = next(
            f.split("=", 1)[1].split(",") for f in flags if f.startswith("--enable-features=")
        )
        self.assertEqual(
            set(enabled),
            {
                "AcceleratedVideoEncoder",
                "AcceleratedVideoDecoder",
                "AcceleratedVideoDecodeLinuxGL",
                "Extra",
                "Legacy",
            },
        )
        self.assertFalse(any(f.startswith("--disable-features=") for f in flags))
        self.assertGreater(
            flags.index("--ozone-platform=x11"), flags.index("--ozone-platform=wayland")
        )

    def test_no_shell_evaluation_and_disable_default(self):
        flags = self.launch(
            " # comment\n --disable-features=AcceleratedVideoEncoder \r\n--example=$(false)\n",
            env={"DPE_DEFAULT_FLAGS": "0"},
        )
        self.assertEqual(
            set(flags), {"--disable-features=AcceleratedVideoEncoder", "--example=$(false)"}
        )
