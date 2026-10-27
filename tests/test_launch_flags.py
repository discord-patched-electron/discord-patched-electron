import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "launch_flags", ROOT / "integration/launcher/flags.py"
)
flags = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flags)


class LaunchFlags(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / "profile"
        self.profile.mkdir()
        self.env = {"XDG_CONFIG_HOME": str(self.root), "WAYLAND_DISPLAY": "wayland-test"}
        self.data = flags.catalog()
        self.config = flags.defaults(self.data)

    def resolve(self, args=(), **request):
        return flags.resolve(
            self.profile,
            "discord-canary-prerelease-patched-electron-bin",
            "discord-canary",
            list(args),
            self.env,
            {"config": self.config, **request},
        )

    def test_defaults_leave_every_experiment_and_forced_codec_off(self):
        result = self.resolve()
        self.assertEqual(result["config"]["active"], "balanced")
        self.assertFalse(
            any(
                v.startswith(
                    ("--enable-features", "--use-angle", "--ignore-gpu", "--enable-hardware")
                )
                for v in result["effective"]
            )
        )
        for preset in self.config["profiles"].values():
            self.assertFalse(preset["decode"])
            self.assertFalse(preset["encode"])
            self.assertEqual(preset["experiments"], [])
            self.assertEqual(preset["backend"], "auto")

    def test_independent_codec_checkboxes_and_customizations_per_preset(self):
        self.config["profiles"]["balanced"].update(decode=True)
        self.config["profiles"]["performance"].update(encode=True, flags="--example=performance")
        result = self.resolve()["effective"]
        self.assertIn(
            "--enable-features=AcceleratedVideoDecoder,AcceleratedVideoDecodeLinuxGL", result
        )
        self.assertFalse(any("AcceleratedVideoEncoder" in v for v in result))
        self.config["active"] = "performance"
        result = self.resolve()["effective"]
        self.assertIn("--enable-features=AcceleratedVideoEncoder", result)
        self.assertIn("--example=performance", result)
        self.assertFalse(any("AcceleratedVideoDecoder" in v for v in result))

    def test_precedence_merges_features_by_name_and_preserves_parameter_values(self):
        self.config["profiles"]["balanced"].update(
            decode=True,
            flags="--disable-features=AcceleratedVideoDecoder\n--enable-features=Keep:Param/a=b",
        )
        self.config["extra"] = "--enable-features=Keep:Param/extra,Extra"
        (self.root / "discord-canary-prerelease-patched-electron-bin-flags.conf").write_text(
            "--disable-features=Extra\n--enable-features=Keep:Param/legacy"
        )
        (self.root / "discord-canary-patched-electron.conf").write_text(
            "--enable-features=Keep<Trial,Final"
        )
        result = self.resolve(["--enable-features=Extra", "--disable-features=Final"])["effective"]
        enabled = next(v for v in result if v.startswith("--enable-features="))
        self.assertIn("Keep<Trial", enabled)
        self.assertNotIn("Keep:Param", enabled)
        self.assertIn("Extra", enabled)
        disabled = next(v for v in result if v.startswith("--disable-features="))
        self.assertIn("AcceleratedVideoDecoder", disabled)
        self.assertIn("Final", disabled)
        self.assertEqual(sum(v.startswith("--enable-features=") for v in result), 1)

    def test_manual_values_are_literal_and_preserve_equals_spaces_and_comments(self):
        self.config["profiles"]["balanced"]["flags"] = (
            " # comment\n--example=$(touch /tmp/not-executed)\n--header=a=b c\n"
        )
        result = self.resolve()["effective"]
        self.assertIn("--example=$(touch /tmp/not-executed)", result)
        self.assertIn("--header=a=b c", result)
        self.assertFalse(any(v.startswith("#") for v in result))

    def test_invalid_editor_flags_have_actionable_errors(self):
        for value, message in [
            ("--enable-features=EnableDrDc", "unsupported"),
            ("--enable-features=EnableDrDc:Mode/x", "unsupported"),
            ("--enable-features=EnableDrDc<Trial", "unsupported"),
            ("--enable-features=Good,", "comma-separated"),
            ("--foo\0bar", "one --name"),
            ("export X=1", "one --name"),
            ("--ignore-flags", "recovery"),
        ]:
            with self.subTest(value=value):
                self.config["profiles"]["balanced"]["flags"] = value
                with self.assertRaisesRegex(ValueError, message):
                    self.resolve()

    def test_explicit_experiments_are_available_without_implicit_opt_ins(self):
        selected = self.config["profiles"]["balanced"]
        selected.update(experiments=["nvidiaVaapi", "gpuBlocklist"], backend="gl")
        result = self.resolve()["effective"]
        self.assertIn("--ignore-gpu-blocklist", result)
        self.assertIn("--use-angle=gl", result)
        self.assertIn("--enable-features=VaapiOnNvidiaGPUs", result)
        self.assertNotIn("--disable-gpu-driver-bug-workarounds", result)

    def test_legacy_custom_flags_migrate_without_old_automatic_gpu_flags(self):
        result, warnings = flags.preferences(
            {
                "openasar": {
                    "cmdPreset": "battery",
                    "customFlags": "--enable-features=EnableDrDc,Keep:Param/a=b --example=value",
                }
            },
            self.data,
        )
        self.assertEqual(result["active"], "efficiency")
        self.assertEqual(result["extra"], "--enable-features=Keep:Param/a=b\n--example=value")
        self.assertTrue(warnings)
        self.assertEqual(result["profiles"]["efficiency"]["experiments"], [])

    def test_invalid_saved_preferences_can_be_repaired_by_preview_and_save(self):
        bad = {"openasar": {"launchFlags": {"schema": 999}}}
        result = flags.resolve(
            self.profile,
            "package",
            "discord-canary",
            [],
            self.env,
            {"settings": bad, "recoverInvalid": True},
        )
        self.assertEqual(result["config"], self.config)
        self.assertIn("Showing defaults", result["warnings"][-1])
        result = self.resolve(settings=bad)
        self.assertEqual(result["config"], self.config)

    def test_malformed_types_are_recoverable_validation_errors(self):
        for value in ([], {}, None, 42):
            with self.subTest(value=value):
                self.config["active"] = value
                with self.assertRaisesRegex(ValueError, "valid preset"):
                    self.resolve()
        self.config["active"] = "balanced"
        self.config["profiles"]["balanced"]["backend"] = []
        with self.assertRaisesRegex(ValueError, "valid rendering backend"):
            self.resolve()

    def test_bad_external_files_fail_launch_but_do_not_prevent_editing_presets(self):
        (self.root / "discord-canary-patched-electron.conf").write_text("not a flag")
        with self.assertRaisesRegex(ValueError, "one --name"):
            self.resolve()
        result = self.resolve(tolerateFileErrors=True)
        self.assertEqual(result["config"], self.config)
        self.assertIn("Fix", result["fileErrors"][0])

    def test_default_flags_opt_out_still_allows_files_and_command_line(self):
        self.env["DPE_DEFAULT_FLAGS"] = "0"
        self.config["profiles"]["balanced"].update(decode=True, encode=True)
        result = self.resolve(["--example=explicit"])
        self.assertEqual(result["effective"], ["--class=discord-canary", "--example=explicit"])

    def test_recovery_preview_distinguishes_current_and_next_normal_launch(self):
        self.env["DPE_IGNORE_FLAGS"] = "1"
        result = self.resolve(["--example=explicit"])
        self.assertEqual(result["effective"], [])
        self.assertIn("--example=explicit", result["normalEffective"])
        self.assertTrue(result["recovery"])

    def test_json_roundtrip_keeps_every_presets_edits(self):
        self.config["profiles"]["efficiency"]["flags"] += "\n--example=kept"
        self.config["profiles"]["performance"]["experiments"] = ["overlays"]
        (self.profile / "settings.json").write_text(
            json.dumps({"openasar": {"launchFlags": self.config}})
        )
        result = flags.resolve(self.profile, "package", "discord-canary", [], self.env)
        self.assertEqual(result["config"], self.config)
