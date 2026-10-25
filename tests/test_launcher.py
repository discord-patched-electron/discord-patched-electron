import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Launcher(unittest.TestCase):
    def launch(self, config="", legacy="", args=(), env=None, details=False, cached=False):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "runtime").mkdir()
            runtime = root / "runtime/electron"
            runtime.write_text(
                "#!/usr/bin/env python3\nimport json,sys,os\n"
                "if os.environ.get('DPE_UPDATER_UI') == '1':\n"
                "    assert 'DPE_CLIENT_HOST' not in os.environ\n"
                "    assert os.environ['PYTHONPATH'].endswith('/python')\n"
                "    sys.exit(0)\n"
                'print(json.dumps({"flags":sys.argv[1:], "profile":os.environ["DPE_PROFILE_DIR"], "desktop":os.environ["CHROME_DESKTOP"], "runtime":os.environ["DPE_RUNTIME_EXECUTABLE"], "app":os.environ["DPE_APP_ROOT"]}))\n'
            )
            runtime.chmod(0o755)
            if cached:
                selected = root / "cached runtime/electron"
                selected.parent.mkdir()
                selected.write_bytes(runtime.read_bytes())
                selected.chmod(0o755)
                updater = root / "integration/updater"
                updater.mkdir(parents=True)
                (root / "client-distribution.json").write_text("{}")
                (updater / "runtime.py").write_text(
                    "import sys\nfrom pathlib import Path\n"
                    "assert '--offline' in sys.argv\n"
                    "print(Path(sys.argv[1]) / 'cached runtime/electron')\n"
                )
                (updater / "client.py").write_text(
                    "import os,sys\n"
                    "assert '--offline' in sys.argv\n"
                    "assert os.environ['PYTHONDONTWRITEBYTECODE'] == '1'\n"
                    "assert os.environ['DPE_RUNTIME_EXECUTABLE'].endswith('/cached runtime/electron')\n"
                    "print(sys.argv[1] + '/generation')\n"
                )
                (updater / "installation.py").write_text(
                    "import sys\nprint(sys.argv[4])\nprint('/fixture/voice')\n"
                )
            name = "discord-canary-prerelease-patched-electron-bin"
            (root / f"{name}-flags.conf").write_text(legacy)
            (root / "discord-canary-patched-electron.conf").write_text(config)
            launcher = root / "launch.sh"
            launcher.write_text(
                (ROOT / "integration/launcher/discord-launcher.sh")
                .read_text()
                .replace("@PKGNAME@", name)
                .replace("@DESKTOP_ID@", "discord-canary")
                .replace("@PROFILE_NAME@", "discordcanary")
                .replace("root=/usr/lib/" + name, "root=" + temp)
            )
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("DPE_", "DISCORD_", "ELECTRON_"))
                and key
                not in ("CHROME_DESKTOP", "LD_PRELOAD", "XDG_SESSION_TYPE", "WAYLAND_DISPLAY")
            }
            result = subprocess.check_output(
                ["bash", str(launcher), *args],
                text=True,
                timeout=10,
                env={
                    **environment,
                    "XDG_CONFIG_HOME": temp,
                    "DPE_CAMERA_FAST_PROBE": "0",
                    "WAYLAND_DISPLAY": "wayland-test",
                    **(env or {}),
                },
            )
            result = json.loads(result)
            return result if details else result["flags"]

    def test_standard_canary_profile_and_explicit_override(self):
        default = self.launch(details=True, env={"DISCORD_USER_DATA_DIR": "", "CHROME_DESKTOP": ""})
        self.assertTrue(default["profile"].endswith("/discordcanary"))
        self.assertEqual(default["desktop"], "discord-canary.desktop")
        self.assertIn("--class=discord-canary", default["flags"])
        custom = self.launch(details=True, env={"DISCORD_USER_DATA_DIR": "/temporary/test-profile"})
        self.assertEqual(custom["profile"], "/temporary/test-profile/discordcanary")
        exact = self.launch(details=True, env={"DPE_PROFILE_DIR": "/temporary/exact"})
        self.assertEqual(exact["profile"], "/temporary/exact")

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

    def test_cached_runtime_preserves_app_root_arguments_and_offline_module_preflight(self):
        result = self.launch(
            args=["--ozone-platform=x11", "--example=with spaces"],
            env={"DPE_CLIENT_OFFLINE": "1"},
            details=True,
            cached=True,
        )
        self.assertEqual(result["runtime"], result["app"] + "/cached runtime/electron")
        self.assertIn("--example=with spaces", result["flags"])
        self.assertIn("--ozone-platform=x11", result["flags"])
        self.assertTrue(result["profile"].endswith("/discordcanary"))

    def test_no_shell_evaluation_and_disable_default(self):
        flags = self.launch(
            " # comment\n --disable-features=AcceleratedVideoEncoder \r\n--example=$(false)\n",
            env={"DPE_DEFAULT_FLAGS": "0"},
        )
        self.assertEqual(
            set(flags),
            {
                "--class=discord-canary",
                "--disable-features=AcceleratedVideoEncoder",
                "--example=$(false)",
            },
        )
