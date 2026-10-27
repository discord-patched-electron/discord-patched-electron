import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_manual_list_keeps_required_defaults_outside_the_editor(self):
        self.config["profiles"]["balanced"].update(flags="--only=this", replaceDefaults=True)
        self.config["extra"] = "--external=kept"
        saved = self.save()
        self.assertIn("--only=this", saved["effective"])
        self.assertIn("--external=kept", saved["effective"])
        self.assertIn("--enable-libopenh264", saved["effective"])
        self.assertNotIn("replaceDefaults", saved["config"]["profiles"]["balanced"])
        self.config["profiles"]["balanced"].pop("replaceDefaults")
        self.assertEqual(self.read()["effective"], saved["effective"])
        self.assertEqual(
            self.read(["--cli=temporary"])["editableFlags"], "--only=this\n--external=kept"
        )
        self.assertIn("--cli=temporary", self.read(["--cli=temporary"])["normalEffective"])
        self.env["DPE_IGNORE_FLAGS"] = "1"
        self.assertEqual(self.read()["effective"], [])

    def test_empty_overrides_keep_dynamic_codec_and_identity_defaults(self):
        result = self.resolve()
        self.assertEqual(result["editableFlags"], "")
        self.assertFalse(any(v.startswith("--autoplay-policy=") for v in result["effective"]))
        self.assertIn("--class=discord-canary", result["effective"])
        self.assertIn("--enable-libopenh264", result["effective"])
        codec = "--openh264-library-path=" + str(
            self.profile / "discord_asset_cache/openh264/libopenh264-2.5.1-linux64.7.so"
        )
        self.assertIn(codec, result["effective"])
        self.config["profiles"]["balanced"]["flags"] = "--custom=value"
        self.save()
        self.config["profiles"]["balanced"]["flags"] = ""
        self.save()
        self.assertEqual(self.read()["editableFlags"], "")
        self.assertIn(codec, self.read()["effective"])
        self.assertNotIn("--custom=value", self.read()["effective"])
        disabled = self.resolve(settings={"openH264Enabled": False})
        self.assertNotIn("--enable-libopenh264", disabled["effective"])
        self.assertFalse(
            any(v.startswith("--openh264-library-path=") for v in disabled["effective"])
        )

    def test_explicit_codec_override_remains_visible_and_takes_precedence(self):
        self.config["extra"] = "--openh264-library-path=/custom/codec.so"
        result = self.resolve()
        self.assertEqual(result["editableFlags"], self.config["extra"])
        self.assertEqual(result["effective"][-1], self.config["extra"])

    def test_essential_templates_expand_per_profile_and_can_be_edited_or_removed(self):
        self.assertIn(
            "{OPENH264_LIBRARY_PATH}", self.config["profiles"]["balanced"]["essentialFlags"]
        )
        self.config["profiles"]["balanced"]["essentialFlags"] = (
            "--class={DESKTOP_ID}\n--openh264-library-path={PROFILE_DIR}/custom codec.so"
        )
        saved = self.save()
        self.assertIn("{PROFILE_DIR}", self.file.read_text())
        self.assertNotIn(str(self.profile), self.file.read_text())
        self.assertIn(
            "--openh264-library-path=" + str(self.profile / "custom codec.so"), saved["effective"]
        )
        self.assertNotIn("--enable-libopenh264", saved["effective"])
        other = flags.resolve(
            self.root / "other profile",
            "discord-canary-prerelease-patched-electron-bin",
            "discord-canary",
            [],
            self.env,
        )
        self.assertIn(
            "--openh264-library-path=" + str(self.root / "other profile/custom codec.so"),
            other["effective"],
        )
        self.config["profiles"]["balanced"]["essentialFlags"] = ""
        self.save()
        self.assertEqual(self.read()["effective"], [])
        self.config["active"] = "performance"
        self.assertIn("--enable-libopenh264", self.resolve()["effective"])

    def test_unknown_variables_fail_without_writing_and_recovery_can_repair_them(self):
        self.save()
        before = self.file.read_bytes()
        self.config["profiles"]["balanced"]["essentialFlags"] = "--path={MISSING}"
        with self.assertRaises(flags.InvalidFlags) as error:
            self.save()
        self.assertEqual(error.exception.field, "essential-flags")
        self.assertEqual(self.file.read_bytes(), before)
        self.file.write_text(flags.render_file(self.config, self.data))
        broken = self.read(recoverInvalid=True, tolerateFileErrors=True)
        self.assertEqual(broken["fileErrorField"], "essential-flags")
        self.assertTrue(broken["fileErrors"])
        self.assertEqual(
            broken["config"]["profiles"]["balanced"]["essentialFlags"], "--path={MISSING}"
        )
        self.config["profiles"]["balanced"]["essentialFlags"] = flags.ESSENTIAL_FLAGS
        self.config["extra"] = "--path={MISSING}"
        with self.assertRaises(flags.InvalidFlags):
            self.save()
        self.file.write_text(flags.render_file(self.config, self.data))
        broken = self.read(recoverInvalid=True, tolerateFileErrors=True)
        self.assertEqual(broken["preservedExtra"], "")
        self.config["extra"] = "# --path={MISSING}\n--path={PROFILE_DIR}"
        self.assertIn("--path=" + str(self.profile), self.save()["effective"])

    def test_inactive_legacy_text_does_not_block_current_edits(self):
        self.config["profiles"]["performance"]["flags"] = "--example={MISSING}"
        self.config["profiles"]["performance"]["essentialFlags"] = "invalid archived flag"
        result = self.save()
        self.assertEqual(
            result["config"]["profiles"]["performance"], self.config["profiles"]["performance"]
        )
        self.config["active"] = "performance"
        with self.assertRaises(flags.InvalidFlags):
            self.save()

    def test_automatic_display_only_overrides_ambiguous_xwayland_selection(self):
        for environment, expected in (
            ({"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"}, []),
            ({"WAYLAND_DISPLAY": "wayland-1"}, []),
            ({"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}, []),
            ({"DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"}, []),
            ({"WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}, ["--ozone-platform=wayland"]),
            ({"WAYLAND_SOCKET": "5", "DISPLAY": ":0"}, ["--ozone-platform=wayland"]),
            ({"WAYLAND_SOCKET": "", "WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"}, []),
            ({"WAYLAND_DISPLAY": "", "DISPLAY": ":0"}, []),
        ):
            with self.subTest(environment=environment):
                self.assertEqual(flags.automatic_display_flags(environment, []), expected)
        self.assertEqual(
            flags.automatic_display_flags({"WAYLAND_DISPLAY": "wayland-1"}, ["--display=:7"]),
            ["--ozone-platform=wayland"],
        )
        (self.root / "wayland-0").touch()
        self.assertEqual(
            flags.automatic_display_flags({"XDG_RUNTIME_DIR": str(self.root), "DISPLAY": ":0"}, []),
            ["--ozone-platform=wayland"],
        )
        for display in ("wayland", "x11"):
            self.config["display"] = display
            result = self.resolve()
            self.assertIn("--ozone-platform=" + display, result["effective"])
            self.assertEqual(result["editableFlags"], "")

    def test_old_stock_presets_and_redundant_options_retire_without_losing_edits(self):
        old = copy.deepcopy(self.data)
        for name, values in old["retiredFlags"].items():
            old["experiments"][name] = {"flags": values}
        self.config["active"] = "performance"
        self.config["profiles"]["performance"].update(
            flags=old["retiredPresetFlags"]["performance"], experiments=["overlays"]
        )
        self.config["profiles"]["efficiency"]["flags"] = "--keep=custom"
        self.file.write_text(flags.render_file(self.config, old))
        loaded = self.read()
        self.assertNotIn("--disable-background-timer-throttling", loaded["effective"])
        self.assertFalse(any("hardware-overlays" in value for value in loaded["effective"]))
        self.assertEqual(loaded["config"]["profiles"]["efficiency"]["flags"], "--keep=custom")
        flags.save_file(loaded, loaded["fileRevision"])
        self.assertEqual(self.read()["effective"], loaded["effective"])

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
        result = self.read(
            ["--enable-features=Extra", "--disable-features=Final"],
            settings={"openasar": {"launchFlags": self.config}},
        )["effective"]
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
        selected.update(experiments=["forceAcceleration"], backend="gl")
        result = self.resolve()["effective"]
        self.assertIn("--ignore-gpu-blocklist", result)
        self.assertIn("--use-angle=gl", result)
        self.assertTrue(any("VaapiOnNvidiaGPUs" in value for value in result))
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

    def test_acceleration_bypass_is_explicit_and_encoding_and_wip_stay_independent(self):
        selected = self.config["profiles"]["balanced"]
        selected["experiments"] = ["forceAcceleration"]
        result = self.resolve()["effective"]
        self.assertIn("--ignore-gpu-blocklist", result)
        self.assertIn("--enable-gpu-rasterization", result)
        enabled = next(v for v in result if v.startswith("--enable-features="))
        for feature in (
            "AcceleratedVideoDecoder",
            "AcceleratedVideoDecodeLinuxGL",
            "VaapiIgnoreDriverChecks",
            "VaapiOnNvidiaGPUs",
        ):
            self.assertIn(feature, enabled)
        self.assertNotIn("AcceleratedVideoEncoder", enabled)
        self.assertNotIn("--enable-skia-graphite", result)
        self.assertNotIn("--disable-gpu-driver-bug-workarounds", result)
        selected["encode"] = True
        self.assertTrue(any("AcceleratedVideoEncoder" in v for v in self.resolve()["effective"]))
        self.config["active"] = "performance"
        self.assertNotIn("--ignore-gpu-blocklist", self.resolve()["effective"])

    def test_nvidia_vaapi_is_independent_and_retained_in_saved_profiles(self):
        self.config["profiles"]["balanced"]["experiments"] = ["nvidiaVaapi"]
        self.file.write_text(flags.render_file(self.config, self.data))
        result = self.read()
        self.assertIn("--enable-features=VaapiOnNvidiaGPUs", result["effective"])
        self.assertEqual(result["config"]["profiles"]["balanced"]["experiments"], ["nvidiaVaapi"])
        self.assertNotIn("--ignore-gpu-blocklist", result["effective"])
        self.assertFalse(any("AcceleratedVideoEncoder" in flag for flag in result["effective"]))

    def test_wip_options_reach_preview_and_launch_without_old_graphite_migration(self):
        selected = self.config["profiles"]["balanced"]
        selected["experiments"] = ["graphite", "graphiteForce", "compositorBuffers"]
        result = self.resolve()
        self.assertEqual(
            result["config"]["profiles"]["balanced"]["experiments"],
            ["graphiteForce"],
        )
        self.assertIn("--enable-skia-graphite", result["effective"])
        self.assertIn("--skia-graphite-dawn-backend=vulkan", result["effective"])
        self.assertNotIn("--enable-gpu-memory-buffer-compositor-resources", result["effective"])
        self.assertNotIn("--ignore-gpu-blocklist", result["effective"])
        self.env["DPE_IGNORE_FLAGS"] = "1"
        recovered = self.resolve()
        self.assertEqual(recovered["effective"], [])
        self.assertEqual(recovered["normalEffective"], result["effective"])

    def test_manual_flags_override_bypass_features_and_disabling_bundle_keeps_individual_options(
        self,
    ):
        selected = self.config["profiles"]["balanced"]
        selected.update(
            experiments=["forceAcceleration", "driverWorkarounds", "graphiteForce"],
            flags="--disable-features=AcceleratedVideoDecodeLinuxGL,VaapiIgnoreDriverChecks\n--disable-gpu-rasterization\n--disable-skia-graphite",
        )
        result = self.resolve()["effective"]
        enabled = next(v for v in result if v.startswith("--enable-features="))
        self.assertNotIn("AcceleratedVideoDecodeLinuxGL", enabled)
        self.assertNotIn("VaapiIgnoreDriverChecks", enabled)
        self.assertIn("--disable-skia-graphite", result)
        self.assertIn("--disable-gpu-rasterization", result)
        selected["experiments"].remove("forceAcceleration")
        result = self.resolve()["effective"]
        self.assertNotIn("--ignore-gpu-blocklist", result)
        self.assertIn("--disable-gpu-driver-bug-workarounds", result)
        self.assertIn("--enable-skia-graphite", result)
        self.assertNotIn("--enable-gpu-rasterization", result)

    def test_retired_options_preserve_edits_without_enabling_stronger_replacements(self):
        self.config["profiles"]["balanced"].update(
            experiments=["graphite", "videoBuffers", "zeroCopy"], flags="--example=keep"
        )
        self.config["profiles"]["performance"]["experiments"] = ["graphite"]
        for request in (
            {"config": self.config},
            {"settings": {"openasar": {"launchFlags": self.config}}},
        ):
            with self.subTest(request=list(request)):
                result = flags.resolve(
                    self.profile, "package", "discord-canary", [], self.env, request
                )
                self.assertEqual(result["config"]["profiles"]["balanced"]["experiments"], [])
                self.assertEqual(result["config"]["profiles"]["performance"]["experiments"], [])
                self.assertIn("--example=keep", result["effective"])
                self.assertNotIn("--enable-zero-copy", result["effective"])
                self.assertFalse(
                    any("graphite" in v.lower() or "video-frames" in v for v in result["effective"])
                )
                self.assertEqual(len(result["warnings"]), 4)
        self.assertEqual(
            self.config["profiles"]["balanced"]["experiments"],
            ["graphite", "videoBuffers", "zeroCopy"],
        )

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
            self.read()
        result = self.read(tolerateFileErrors=True)
        self.assertEqual(result["config"]["extra"], "not a flag")
        self.assertIn("Fix", result["fileErrors"][0])
        self.config = result["config"]
        self.config["extra"] = "--repaired"
        self.save()
        self.assertIn("--repaired", self.read()["effective"])

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
        self.config["profiles"]["performance"]["experiments"] = ["driverWorkarounds"]
        (self.profile / "settings.json").write_text(
            json.dumps({"openasar": {"launchFlags": self.config}})
        )
        result = flags.resolve(self.profile, "package", "discord-canary", [], self.env)
        self.assertEqual(result["config"], self.config)

    def read(self, args=(), **request):
        return flags.resolve(
            self.profile,
            "discord-canary-prerelease-patched-electron-bin",
            "discord-canary",
            list(args),
            self.env,
            request,
        )

    def save(self):
        result = self.resolve()
        return flags.save_file(result, result["fileRevision"])

    @property
    def file(self):
        return self.root / "discord-canary-patched-electron.conf"

    def test_file_roundtrip_preserves_all_presets_and_independent_codec_controls(self):
        for decode, encode in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(decode=decode, encode=encode):
                self.config["profiles"]["balanced"].update(decode=decode, encode=encode)
                self.config["profiles"]["efficiency"]["flags"] = "# inactive\n--example=kept"
                self.config["extra"] = "# 手動 flags\n--extra=value\n\n"
                saved = self.save()
                result = self.read()
                self.assertEqual(result["config"], self.config)
                self.assertEqual(saved["fileRevision"], result["fileRevision"])
                self.assertEqual(saved["effective"], result["effective"])
                self.assertEqual(
                    any("AcceleratedVideoDecoder" in v for v in result["effective"]), decode
                )
                self.assertEqual(
                    any("AcceleratedVideoEncoder" in v for v in result["effective"]), encode
                )
                self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)
                # A plain flags-file reader can also see the active options.
                raw = flags.lines(self.file.read_text())
                self.assertEqual(any("AcceleratedVideoDecoder" in v for v in raw), decode)
                self.assertEqual(any("AcceleratedVideoEncoder" in v for v in raw), encode)

    def test_migration_preserves_precedence_and_backs_up_existing_plain_file(self):
        self.config["extra"] = "--example=settings"
        self.config["profiles"]["balanced"].update(decode=True)
        legacy = self.root / "discord-canary-prerelease-patched-electron-bin-flags.conf"
        legacy.write_text("# older file\n--example=legacy")
        original = "# hand written\n--example=canonical\n"
        self.file.write_text(original)
        self.file.chmod(0o640)
        (self.profile / "settings.json").write_text(
            json.dumps({"openasar": {"launchFlags": self.config}})
        )
        loaded = self.read()
        before = loaded["effective"]
        self.assertEqual(
            [f for f in before if f.startswith("--example=")],
            ["--example=settings", "--example=legacy", "--example=canonical"],
        )
        flags.save_file(loaded, loaded["fileRevision"])
        self.assertEqual(self.read()["effective"], before)
        self.assertEqual(next(self.root.glob("*.backup-*")).read_text(), original)
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o640)
        legacy.write_text("--example=stale")
        (self.profile / "settings.json").write_text(
            json.dumps({"openasar": {"launchFlags": {"schema": 999}}})
        )
        self.assertEqual(self.read()["effective"], before)

    def test_manual_active_edits_win_without_losing_other_presets(self):
        self.config["profiles"]["balanced"].update(decode=True, experiments=["forceAcceleration"])
        self.config["profiles"]["performance"]["flags"] = "--keep=inactive"
        self.save()
        text = self.file.read_text()
        a = text.index("\n", text.index(flags.META)) + 1
        b = text.index(flags.END)
        self.file.write_text(text[:a] + "# edited active body\n--disable-gpu\n" + text[b:])
        result = self.read()
        self.assertIn("--disable-gpu", result["effective"])
        self.assertNotIn("--ignore-gpu-blocklist", result["effective"])
        selected = result["config"]["profiles"]["balanced"]
        self.assertFalse(selected["decode"])
        self.assertEqual(selected["experiments"], [])
        self.assertEqual(result["config"]["profiles"]["performance"]["flags"], "--keep=inactive")
        self.assertIn("edited in the file", result["warnings"][0])
        self.config = result["config"]
        self.save()
        self.assertEqual(self.read()["config"], self.config)

    def test_outside_edits_are_additional_flags_and_override_checkboxes(self):
        self.config["profiles"]["balanced"]["decode"] = True
        self.save()
        outside = "# manual override\n--disable-features=AcceleratedVideoDecoder\n"
        with self.file.open("a") as stream:
            stream.write(outside)
        result = self.read()
        self.assertTrue(result["config"]["profiles"]["balanced"]["decode"])
        self.assertEqual(result["config"]["extra"], outside)
        self.assertIn("--disable-features=AcceleratedVideoDecoder", result["effective"])
        self.env["DPE_DEFAULT_FLAGS"] = "0"
        self.assertEqual(
            self.read()["effective"],
            ["--class=discord-canary", "--disable-features=AcceleratedVideoDecoder"],
        )

    def test_stale_save_is_rejected_without_overwriting_external_edits(self):
        self.save()
        draft = self.resolve()
        external = self.file.read_text() + "--external=new\n"
        self.file.write_text(external)
        with self.assertRaisesRegex(ValueError, "changed on disk"):
            flags.save_file(draft, draft["fileRevision"])
        self.assertEqual(self.file.read_text(), external)
        self.assertEqual(list(self.root.glob("*.tmp-*")), [])

    def test_symlink_preserved_and_retarget_detected(self):
        target = self.root / "shared.conf"
        target.write_text("--existing\n")
        self.file.symlink_to(target.name)
        self.config = self.read()["config"]
        self.save()
        self.assertTrue(self.file.is_symlink())
        self.assertIn(flags.BEGIN, target.read_text())
        draft = self.resolve()
        other = self.root / "other.conf"
        other.write_bytes(target.read_bytes())
        self.file.unlink()
        self.file.symlink_to(other.name)
        with self.assertRaisesRegex(ValueError, "changed on disk"):
            flags.save_file(draft, draft["fileRevision"])

    def test_atomic_save_failure_preserves_file_and_cleans_temporary(self):
        self.save()
        original = self.file.read_bytes()
        self.config["extra"] = "--new"
        draft = self.resolve()
        with mock.patch.object(flags.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaisesRegex(OSError, "disk error"):
                flags.save_file(draft, draft["fileRevision"])
        self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(list(self.root.glob("*.tmp-*")), [])

    def test_corrupt_metadata_can_be_repaired_without_losing_manual_flags(self):
        original = (
            flags.BEGIN
            + "\n"
            + flags.META
            + "{bad}\n--active\n"
            + flags.END
            + "\n# manual\n--outside\n"
        )
        self.file.write_text(original)
        with self.assertRaisesRegex(ValueError, "ignore-flags"):
            self.read()
        self.env["DPE_IGNORE_FLAGS"] = "1"
        result = self.read(recoverInvalid=True)
        self.assertEqual(result["effective"], [])
        self.assertIn("--outside", result["normalEffective"])
        self.assertIn("--active", result["normalEffective"])
        self.config = result["config"]
        self.save()
        self.assertEqual(next(self.root.glob("*.backup-*")).read_text(), original)
        self.assertTrue(self.read()["fileManaged"])

    def test_reserved_markers_cannot_corrupt_saved_section(self):
        self.save()
        original = self.file.read_bytes()
        self.config["extra"] = flags.BEGIN
        with self.assertRaisesRegex(ValueError, "reserved"):
            self.save()
        self.assertEqual(self.file.read_bytes(), original)

    def test_missing_metadata_preserves_active_and_outside_flags_for_repair(self):
        self.file.write_text(flags.BEGIN + "\n--inside\n" + flags.END + "\n--outside\n")
        result = self.read(recoverInvalid=True)
        self.assertIn("--inside", result["effective"])
        self.assertIn("--outside", result["effective"])

    def test_save_revision_describes_our_write_even_if_an_editor_writes_immediately_after(self):
        self.save()
        draft = self.resolve()
        replace = flags.os.replace

        def external_edit(source, target):
            replace(source, target)
            with self.file.open("a") as stream:
                stream.write("--external\n")

        with mock.patch.object(flags.os, "replace", side_effect=external_edit):
            saved = flags.save_file(draft, draft["fileRevision"])
        self.assertNotEqual(saved["fileRevision"], self.read()["fileRevision"])
        with self.assertRaisesRegex(ValueError, "changed on disk"):
            flags.save_file(saved, saved["fileRevision"])

    def test_manual_crash_reproduction_flags_still_load_literally(self):
        self.file.write_text("--enable-features=EnableDrDc\n")
        self.assertIn("--enable-features=EnableDrDc", self.read()["effective"])
