import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Launcher(unittest.TestCase):
    def launch(
        self,
        config="",
        legacy="",
        args=(),
        env=None,
        details=False,
        cached=False,
        unbundled=False,
        settings=None,
        broken_resolver=False,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(ROOT / "integration/launcher", root / "integration/launcher")
            if broken_resolver:
                (root / "integration/launcher/flags.py").write_text(
                    "raise RuntimeError('must not run')"
                )
            if settings is not None:
                (root / "discordcanary").mkdir()
                (root / "discordcanary/settings.json").write_text(
                    settings if isinstance(settings, str) else json.dumps(settings)
                )
            (root / "runtime").mkdir()
            runtime = root / "runtime/electron"
            runtime.write_text(
                "#!/usr/bin/env python3\nimport json,sys,os\n"
                "if os.environ.get('DPE_UPDATER_UI') == '1':\n"
                "    assert 'DPE_CLIENT_HOST' not in os.environ\n"
                "    assert os.environ['PYTHONPATH'].endswith('/python')\n"
                "    from pathlib import Path\n"
                "    root = Path(os.environ['DPE_APP_ROOT'])\n"
                "    Path(os.environ['DPE_LAUNCH_RESULT']).write_text(json.dumps({'runtime': str(root / 'cached runtime/electron'), 'installed_runtime': str(root / 'cached runtime/electron'), 'host': str(root / 'prepared'), 'voice': '/fixture/voice'}))\n"
                "    sys.exit(int(os.environ.get('DPE_FIXTURE_UI_STATUS', '0')))\n"
                'print(json.dumps({"flags":sys.argv[1:], "originalArgs":json.loads(os.environ["DPE_LAUNCH_ARGS"]), "managed":os.environ["DPE_FLAGS_MANAGED"], "profile":os.environ["DPE_PROFILE_DIR"], "desktop":os.environ["CHROME_DESKTOP"], "runtime":os.environ["DPE_RUNTIME_EXECUTABLE"], "app":os.environ["DPE_APP_ROOT"]}))\n'
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
                for source in (ROOT / "integration/updater").glob("*.py"):
                    (updater / source.name).write_bytes(source.read_bytes())
                if unbundled:
                    runtime.unlink()
                    (root / "resources").mkdir()
                    policy = {
                        "channel": "canary",
                        "electron_channel": "prerelease",
                        "electron_version": "46.0.0-alpha.1",
                        "runtime_key": "a" * 64,
                        "archive_sha256": "b" * 64,
                    }
                    (root / "runtime-update.json").write_text(json.dumps(policy))
                    identity = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
                    cache = root / "discordcanary/dpe-runtime" / identity
                    generation = cache / "generations" / ("b" * 64)
                    generation.mkdir(parents=True)
                    executable = generation / "electron"
                    executable.write_bytes(selected.read_bytes())
                    executable.chmod(0o700)
                    for name in (
                        "icudtl.dat",
                        "resources.pak",
                        "LICENSE",
                        "LICENSES.chromium.html",
                    ):
                        (generation / name).write_bytes(b"fixture")
                    (generation / "resources").symlink_to(root / "resources")
                    (generation / "runtime.json").write_text(
                        json.dumps(
                            {
                                **policy,
                                "executable_sha256": hashlib.sha256(
                                    executable.read_bytes()
                                ).hexdigest(),
                            }
                        )
                    )
                    (cache / "current.json").write_text(json.dumps("b" * 64))
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
            if not result:
                return None
            result = json.loads(result)
            if details:
                result["flagsFileContent"] = (
                    root / "discord-canary-patched-electron.conf"
                ).read_text()
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
                "Extra",
                "Legacy",
            },
        )
        self.assertFalse(any(f.startswith("--disable-features=") for f in flags))
        self.assertGreater(
            flags.index("--ozone-platform=x11"), flags.index("--ozone-platform=wayland")
        )

    def test_prepared_launch_preserves_app_root_arguments_and_runtime_selection(self):
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
        self.assertEqual(result["originalArgs"], ["--ozone-platform=x11", "--example=with spaces"])
        self.assertEqual(result["managed"], "1")

    def test_recovery_bypasses_even_broken_resolver_settings_files_and_cli(self):
        for env, args in [
            ({}, ["--ignore-flags", "--enable-features=EnableDrDc"]),
            ({"DPE_IGNORE_FLAGS": "1"}, ["--use-angle=broken"]),
        ]:
            with self.subTest(env=env, args=args):
                result = self.launch(
                    "not valid flags",
                    legacy="also invalid",
                    settings="{broken json",
                    broken_resolver=True,
                    args=args,
                    env=env,
                    details=True,
                    cached=True,
                )
                self.assertEqual(result["flags"], [])
                self.assertEqual(result["originalArgs"], ["--ignore-flags"])
                self.assertEqual(result["flagsFileContent"], "not valid flags")

    def test_saved_presets_are_resolved_before_launch(self):
        result = self.launch(
            settings={
                "openasar": {
                    "cmdPreset": "perf",
                    "customFlags": "--enable-features=Custom:Param/value",
                }
            },
            details=True,
        )
        self.assertIn("--disable-renderer-backgrounding", result["flags"])
        self.assertIn("--enable-features=Custom:Param/value", result["flags"])
        self.assertEqual(result["originalArgs"], [])
        self.assertNotIn("--ignore-gpu-blocklist", result["flags"])
        self.assertIn("# >>> OpenAsar launch presets v1", result["flagsFileContent"])
        self.assertIn("--disable-renderer-backgrounding", result["flagsFileContent"])
        relaunched = self.launch(
            config=result["flagsFileContent"],
            legacy="--stale-package-flag",
            settings={"openasar": {"cmdPreset": "battery", "customFlags": "--stale-setting"}},
        )
        self.assertIn("--disable-renderer-backgrounding", relaunched)
        self.assertIn("--enable-features=Custom:Param/value", relaunched)
        self.assertNotIn("--stale-package-flag", relaunched)
        self.assertNotIn("--stale-setting", relaunched)

    def test_second_instance_exits_successfully_without_starting_another_runtime(self):
        self.assertIsNone(self.launch(env={"DPE_FIXTURE_UI_STATUS": "2"}, cached=True))

    def test_runtime_free_launcher_uses_cached_electron_for_startup_and_final_client(self):
        result = self.launch(
            args=["--example=with spaces"],
            details=True,
            cached=True,
            unbundled=True,
            env={"DPE_CLIENT_OFFLINE": "1"},
        )
        self.assertEqual(result["runtime"], result["app"] + "/cached runtime/electron")
        self.assertIn("--example=with spaces", result["flags"])

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
