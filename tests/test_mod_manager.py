"""The settings mod installer changes only a verified active profile loader."""

import fcntl
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

from mod_fixture import loader

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration/updater"))
import mod_manager as mods


@lru_cache
def equicord_payload():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        for name in mods.REQUIRED_FILES:
            (source / name).write_text("// Equicord abcdef1234567890\n/* fixture */")
        (source / "package.json").write_text('{"name":"equicord","main":"patcher.js"}')
        target = root / "desktop.asar"
        mods.subprocess.run(
            [
                str(Path(__file__).resolve().parents[1] / "node_modules/.bin/asar"),
                "pack",
                str(source),
                str(target),
            ],
            check=True,
        )
        return target.read_bytes()


class ModManager(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.profile = Path(temporary.name)
        (self.profile / "dpe-updater").mkdir()
        self.resources = self.profile / "app-1.0.10/resources"
        self.resources.mkdir(parents=True)
        self.bootstrap = b"healthy project bootstrap"
        (self.resources / "app.asar").write_bytes(self.bootstrap)
        (self.resources / "build_info.json").write_text('{"releaseChannel":"canary"}')
        with sqlite3.connect(self.profile / "installer.db") as connection:
            connection.execute("CREATE TABLE key_values (key TEXT, value TEXT)")
            connection.execute(
                "INSERT INTO key_values VALUES (?, ?)",
                ("dpe/active/canary", json.dumps({"version": "1.0.10"})),
            )
        self.payload = self.profile / "custom mods/dist/patcher.js"
        self.payload.parent.mkdir(parents=True)
        self.payload.write_text("/* fixture */")
        self.vencord_loader = loader("Vencord", target=str(self.payload))
        self.equicord_payload = equicord_payload()
        self.real_run = mods.subprocess.run

    def install_existing(self, name="Vencord"):
        data = loader(name, target=str(self.payload) if name == "Vencord" else None)
        (self.resources / "_app.asar").write_bytes(self.bootstrap)
        (self.resources / "app.asar").write_bytes(data)
        return data

    def fake_install(self, args, **kwargs):
        staged = Path(args[-1]) / "resources"
        self.assertNotEqual(staged, self.resources)
        self.assertEqual(kwargs["stdin"], mods.subprocess.DEVNULL)
        generation = Path(kwargs["env"]["EQUICORD_USER_DATA_DIR"])
        self.assertEqual(generation.parent, self.profile / "dpe-updater/mod-payloads")
        self.assertEqual(kwargs["env"]["VENCORD_USER_DATA_DIR"], str(generation))
        self.assertEqual(kwargs["env"]["EQUICORD_DIRECTORY"], str(generation / "equicord.asar"))
        self.assertNotIn("VENCORD_DEV_INSTALL", kwargs["env"])
        self.assertNotIn("EQUICORD_DEV_INSTALL", kwargs["env"])
        choice = generation.name.split("-")[0]
        target = mods.payload_target(generation, choice)
        if choice == "vencord":
            target.parent.mkdir()
            for name in mods.REQUIRED_FILES:
                (target.parent / name).write_text("// Vencord abcdef1234567890\n/* fixture */")
            (target.parent / "package.json").write_text("{}")
        else:
            target.write_bytes(self.equicord_payload)
        (staged / "app.asar").rename(staged / "_app.asar")
        with patch.object(mods.subprocess, "run", self.real_run):
            wrapper = loader(choice.capitalize(), target=str(target))
        (staged / "app.asar").write_bytes(wrapper)
        return mods.subprocess.CompletedProcess(args, 0, stdout=b"Success")

    def test_complete_creates_preferences_directory_for_existing_install(self):
        (self.profile / "dpe-updater").rmdir()
        self.assertTrue(mods.complete(self.profile, "canary")["setupComplete"])
        self.assertTrue(mods.status(self.profile, "canary")["setupComplete"])

    def test_pending_welcome_install_and_remove_do_not_complete_onboarding(self):
        started = mods.complete(self.profile, "canary", pending=True)
        self.assertTrue(started["onboardingPending"])
        with (
            patch.object(mods, "installer", return_value=Path("/fixture/installer")),
            patch.object(mods.subprocess, "run", side_effect=self.fake_install),
        ):
            installed = mods.apply(self.profile, "canary", "vencord")
        self.assertFalse(installed["setupComplete"])
        self.assertTrue(installed["onboardingPending"])
        self.assertFalse(mods.apply(self.profile, "canary", "none")["setupComplete"])
        completed = mods.complete(self.profile, "canary")
        self.assertTrue(completed["setupComplete"])
        self.assertFalse(completed["onboardingPending"])

    def test_status_uses_active_db_version_not_newest_or_backups(self):
        previous = self.profile / "app-1.0.99/resources"
        previous.mkdir(parents=True)
        (previous / "app.asar").write_bytes(loader())
        self.assertEqual(mods.status(self.profile, "canary")["installed"], "none")
        self.assertFalse(mods.status(self.profile, "canary")["setupComplete"])
        self.install_existing()
        self.assertEqual(mods.status(self.profile, "canary")["installed"], "vencord")

    def test_installed_version_uses_build_banner_and_honest_metadata_fallback(self):
        self.assertIsNone(mods.status(self.profile, "canary")["installedVersion"])
        self.install_existing()
        self.assertIsNone(mods.status(self.profile, "canary")["installedVersion"])
        self.payload.write_text("// Vencord abcdef1234567\nthrow Error('must never execute')")
        self.assertEqual(mods.status(self.profile, "canary")["installedVersion"], "abcdef1")
        self.payload.write_text("// Equicord abcdef1234567\n")
        self.assertIsNone(mods.status(self.profile, "canary")["installedVersion"])
        package = self.payload.parent / "package.json"
        package.write_text('{"version":"1.2.3-beta.1"}')
        self.assertEqual(mods.status(self.profile, "canary")["installedVersion"], "1.2.3-beta.1")
        for value in ['{"version":"unverified"}', '{"version":false}', " " * 16385]:
            package.write_text(value)
            self.assertIsNone(mods.status(self.profile, "canary")["installedVersion"])

    def test_equicord_build_banner_is_read_from_bounded_archive_member(self):
        source = self.profile / "equicord-source"
        source.mkdir()
        (source / "package.json").write_text('{"name":"equicord","main":"patcher.js"}')
        (source / "patcher.js").write_text(
            "// Equicord abcdef1234567890\nthrow Error('never execute')"
        )
        target = self.profile / "custom-desktop.asar"
        mods.subprocess.run(
            [
                str(Path(__file__).resolve().parents[1] / "node_modules/.bin/asar"),
                "pack",
                str(source),
                str(target),
            ],
            check=True,
        )
        (self.resources / "app.asar").write_bytes(loader("Equicord", target=str(target)))
        self.assertEqual(mods.status(self.profile, "canary")["installedVersion"], "abcdef1")
        valid = target.read_bytes()
        for corrupt in [b"", b"not an archive", valid[:16], b"\xff" * 16 + valid[16:]]:
            target.write_bytes(corrupt)
            result = mods.status(self.profile, "canary")
            self.assertEqual(result["installed"], "equicord")
            self.assertIsNone(result["installedVersion"])

    def test_offline_mode_preserves_installed_mod_and_allows_removal(self):
        original = self.install_existing()
        with (
            patch.dict(mods.os.environ, {"DPE_CLIENT_OFFLINE": "1"}),
            patch.object(mods, "installer") as download,
        ):
            with self.assertRaisesRegex(ValueError, "internet connection"):
                mods.apply(self.profile, "canary", "equicord")
            download.assert_not_called()
            self.assertEqual((self.resources / "app.asar").read_bytes(), original)
            self.assertEqual(mods.apply(self.profile, "canary", "none")["installed"], "none")

    def test_install_publishes_verified_loader_and_leaves_bootstrap_backup(self):
        with (
            patch.object(mods, "installer", return_value=Path("/fixture/installer")),
            patch.object(mods.subprocess, "run", side_effect=self.fake_install),
        ):
            result = mods.apply(self.profile, "canary", "vencord")
        self.assertEqual(result["installed"], "vencord")
        self.assertTrue(result["setupComplete"])
        self.assertEqual((self.resources / "_app.asar").read_bytes(), self.bootstrap)

    def test_switch_does_not_expose_original_loader_to_installer(self):
        old = self.install_existing("Equicord")

        def install(args, **kwargs):
            self.assertEqual((self.resources / "app.asar").read_bytes(), old)
            self.fake_install(args, **kwargs)

        with (
            patch.object(mods, "installer", return_value=Path("/fixture/installer")),
            patch.object(mods.subprocess, "run", side_effect=install),
        ):
            result = mods.apply(self.profile, "canary", "vencord")
        self.assertEqual(result["installed"], "vencord")
        self.assertEqual((self.resources / "_app.asar").read_bytes(), self.bootstrap)

    def test_install_failure_preserves_existing_mod_and_setup_state(self):
        old = self.install_existing("Equicord")
        with (
            patch.object(mods, "installer", return_value=Path("/fixture/installer")),
            patch.object(
                mods.subprocess, "run", side_effect=mods.subprocess.TimeoutExpired("fixture", 120)
            ),
            self.assertRaisesRegex(ValueError, "current mod is unchanged"),
        ):
            mods.apply(self.profile, "canary", "vencord")
        self.assertEqual((self.resources / "app.asar").read_bytes(), old)
        self.assertEqual((self.resources / "_app.asar").read_bytes(), self.bootstrap)
        self.assertFalse(mods.status(self.profile, "canary")["setupComplete"])

    def test_invalid_installer_output_cannot_replace_live_loader(self):
        old = self.install_existing("Equicord")
        with (
            patch.object(mods, "installer", return_value=Path("/fixture/installer")),
            patch.object(mods.subprocess, "run"),
            self.assertRaisesRegex(ValueError, "working mod"),
        ):
            mods.apply(self.profile, "canary", "vencord")
        self.assertEqual((self.resources / "app.asar").read_bytes(), old)

    def test_same_mod_does_not_reinstall_or_change_custom_target(self):
        old = self.install_existing()
        with patch.object(mods, "installer") as download:
            result = mods.apply(self.profile, "canary", "vencord")
        download.assert_not_called()
        self.assertEqual((self.resources / "app.asar").read_bytes(), old)
        self.assertTrue(result["setupComplete"])

    def test_skip_keeps_existing_mod_and_uninstall_keeps_mod_data(self):
        old = self.install_existing()
        self.assertTrue(mods.complete(self.profile, "canary")["setupComplete"])
        self.assertEqual((self.resources / "app.asar").read_bytes(), old)
        result = mods.apply(self.profile, "canary", "none")
        self.assertEqual(result["installed"], "none")
        self.assertEqual((self.resources / "app.asar").read_bytes(), self.bootstrap)
        self.assertFalse((self.resources / "_app.asar").exists())
        self.assertTrue(self.payload.is_file())

    def test_missing_backup_and_unknown_leftovers_are_not_overwritten(self):
        self.install_existing()
        (self.resources / "_app.asar").unlink()
        with self.assertRaisesRegex(ValueError, "original Discord files"):
            mods.apply(self.profile, "canary", "none")
        (self.resources / "app.asar").write_bytes(self.bootstrap)
        (self.resources / "_app.asar").write_bytes(b"unknown backup")
        with self.assertRaisesRegex(ValueError, "unrecognized mod backup"):
            mods.apply(self.profile, "canary", "vencord")

    def test_busy_update_cannot_race_mod_change(self):
        with (self.profile / "dpe-updater/installation.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaisesRegex(ValueError, "Discord is updating"):
                mods.apply(self.profile, "canary", "vencord")

    def test_untrusted_choice_and_symlinked_installation_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Choose Vencord"):
            mods.apply(self.profile, "canary", "../custom")
        renamed = self.resources.with_name("original")
        self.resources.rename(renamed)
        self.resources.symlink_to(renamed)
        with self.assertRaisesRegex(ValueError, "local directory"):
            mods.status(self.profile, "canary")

    def test_interrupted_backup_first_install_is_recoverable(self):
        (self.resources / "_app.asar").write_bytes(self.bootstrap)
        with (
            patch.object(mods, "installer", return_value=Path("/fixture/installer")),
            patch.object(mods.subprocess, "run", side_effect=self.fake_install),
        ):
            self.assertEqual(mods.apply(self.profile, "canary", "vencord")["installed"], "vencord")
        (self.resources / "app.asar").write_bytes(self.bootstrap)
        self.assertEqual(mods.apply(self.profile, "canary", "none")["installed"], "none")
        self.assertFalse((self.resources / "_app.asar").exists())

    def test_installer_hash_is_checked_before_execution(self):
        with (
            patch.object(
                mods, "download", side_effect=lambda _url, path, **_kw: path.write_bytes(b"wrong")
            ),
            self.assertRaisesRegex(ValueError, "did not pass verification"),
        ):
            mods.installer(self.profile, "vencord")
        self.assertEqual(list((self.profile / "dpe-updater/mod-installers").iterdir()), [])

    def test_both_mods_use_complete_isolated_payloads_and_reuse_verified_cache(self):
        for choice in ("vencord", "equicord"):
            with self.subTest(choice=choice):
                mods.apply(self.profile, "canary", "none")
                with (
                    patch.object(mods, "installer", return_value=Path("/fixture/installer")),
                    patch.object(mods.subprocess, "run", side_effect=self.fake_install) as run,
                ):
                    result = mods.apply(self.profile, "canary", choice)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(result["installed"], choice)
                self.assertEqual(result["installedVersion"], "abcdef1")
                first = mods.detect(self.resources)
                self.assertTrue(Path(first.target).is_file())
                mods.apply(self.profile, "canary", "none")
                with (
                    patch.dict(os.environ, {"DPE_CLIENT_OFFLINE": "1"}),
                    patch.object(mods, "installer") as download,
                ):
                    self.assertEqual(
                        mods.apply(self.profile, "canary", choice)["installed"], choice
                    )
                download.assert_not_called()
                self.assertEqual(mods.detect(self.resources), first)

    def test_failed_external_payload_write_preserves_both_existing_data_directories(self):
        equicord = self.profile / "old-equicord/equicord.asar"
        equicord.parent.mkdir()
        equicord.write_bytes(self.equicord_payload)
        original_vencord = self.payload.read_bytes()
        environment = {
            "VENCORD_USER_DATA_DIR": str(self.payload.parent.parent),
            "EQUICORD_USER_DATA_DIR": str(equicord.parent),
            "EQUICORD_DIRECTORY": str(equicord),
            "VENCORD_DEV_INSTALL": "1",
            "EQUICORD_DEV_INSTALL": "1",
        }

        def truncate_then_fail(args, **kwargs):
            generation = Path(kwargs["env"]["VENCORD_USER_DATA_DIR"])
            self.assertEqual(generation.parent, self.profile / "dpe-updater/mod-payloads")
            for name in ("VENCORD_DEV_INSTALL", "EQUICORD_DEV_INSTALL"):
                self.assertNotIn(name, kwargs["env"])
            target = mods.payload_target(generation, generation.name.split("-")[0])
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(b"partial download")
            raise mods.subprocess.CalledProcessError(1, args, output=b"unexpected EOF")

        for choice, existing in (("vencord", "Equicord"), ("equicord", "Vencord")):
            with self.subTest(choice=choice):
                old = self.install_existing(existing)
                with (
                    patch.dict(os.environ, environment),
                    patch.object(mods, "installer", return_value=Path("/fixture/installer")),
                    patch.object(mods.subprocess, "run", side_effect=truncate_then_fail),
                    self.assertRaisesRegex(ValueError, "download did not finish"),
                ):
                    mods.apply(self.profile, "canary", choice)
                self.assertEqual((self.resources / "app.asar").read_bytes(), old)
                self.assertEqual(self.payload.read_bytes(), original_vencord)
                self.assertEqual(equicord.read_bytes(), self.equicord_payload)
                self.assertEqual(list((self.profile / "dpe-updater/mod-payloads").iterdir()), [])
                self.assertFalse(mods.status(self.profile, "canary")["setupComplete"])

    def test_cache_expiry_self_updates_and_installer_changes_do_not_restore_old_bytes(self):
        with (
            patch.object(mods, "installer", return_value=Path("/fixture/installer")),
            patch.object(mods.subprocess, "run", side_effect=self.fake_install) as run,
        ):
            mods.apply(self.profile, "canary", "vencord")
            first = mods.detect(self.resources)
            mods.apply(self.profile, "canary", "none")
            Path(first.target).write_bytes(b"self updated contents")
            mods.apply(self.profile, "canary", "vencord")
            self.assertEqual(run.call_count, 2)
            self.assertNotEqual(mods.detect(self.resources).target, first.target)
            self.assertEqual(Path(first.target).read_bytes(), b"self updated contents")
            mods.apply(self.profile, "canary", "none")
            with patch.object(mods.time, "time", return_value=mods.time.time() + 1801):
                mods.apply(self.profile, "canary", "vencord")
            self.assertEqual(run.call_count, 3)
            mods.apply(self.profile, "canary", "none")
            manifest = self.profile / "dpe-updater/mod-payloads/vencord.json"
            metadata = json.loads(manifest.read_text())
            metadata["created"] = mods.time.time()
            manifest.write_text(json.dumps(metadata))
            with patch.dict(mods.INSTALLERS, {"vencord": ("https://fixture.invalid", "0" * 64)}):
                mods.apply(self.profile, "canary", "vencord")
            self.assertEqual(run.call_count, 4)

    def test_payload_verification_rejects_missing_files_truncated_asar_and_external_targets(self):
        for choice, damage in (
            ("vencord", "missing"),
            ("equicord", "truncated"),
            ("vencord", "symlink"),
            ("vencord", "external"),
        ):
            with self.subTest(choice=choice, damage=damage):

                def invalid(args, choice=choice, damage=damage, **kwargs):
                    result = self.fake_install(args, **kwargs)
                    generation = Path(kwargs["env"]["VENCORD_USER_DATA_DIR"])
                    target = mods.payload_target(generation, choice)
                    if damage == "missing":
                        (target.parent / "renderer.js").unlink()
                    elif damage == "truncated":
                        target.write_bytes(target.read_bytes()[:-12])
                    elif damage == "symlink":
                        target.unlink()
                        target.symlink_to(self.payload)
                    else:
                        (Path(args[-1]) / "resources/app.asar").write_bytes(self.vencord_loader)
                    return result

                with (
                    patch.object(mods, "installer", return_value=Path("/fixture/installer")),
                    patch.object(mods.subprocess, "run", side_effect=invalid),
                    self.assertRaisesRegex(ValueError, "working mod"),
                ):
                    mods.apply(self.profile, "canary", choice)
                self.assertEqual((self.resources / "app.asar").read_bytes(), self.bootstrap)
                self.assertEqual(list((self.profile / "dpe-updater/mod-payloads").iterdir()), [])

    def test_failed_activation_keeps_live_mod_and_cleans_only_new_generation(self):
        old = self.install_existing("Equicord")
        real_replace = mods.replace_file

        def fail_loader(target, contents):
            if target.name == "app.asar":
                raise OSError("fixture disk failure")
            real_replace(target, contents)

        with (
            patch.object(mods, "installer", return_value=Path("/fixture/installer")),
            patch.object(mods.subprocess, "run", side_effect=self.fake_install),
            patch.object(mods, "replace_file", side_effect=fail_loader),
            self.assertRaisesRegex(OSError, "disk failure"),
        ):
            mods.apply(self.profile, "canary", "vencord")
        self.assertEqual((self.resources / "app.asar").read_bytes(), old)
        self.assertTrue(self.payload.is_file())
        self.assertIsNone(mods.cached_payload(self.profile / "dpe-updater", "vencord"))
        self.assertEqual(list((self.profile / "dpe-updater/mod-payloads").glob("vencord-*")), [])

    def test_cli_rate_limit_is_not_retried_and_cooldown_applies_across_mod_choices(self):
        with (
            patch.object(mods.time, "time", return_value=1000),
            patch.object(mods, "installer", return_value=Path("/fixture/installer")) as download,
            patch.object(
                mods.subprocess,
                "run",
                side_effect=mods.subprocess.CalledProcessError(
                    1, "fixture", output=b"GitHub returned status code 403 (rate limited)"
                ),
            ) as run,
        ):
            with self.assertRaisesRegex(ValueError, "temporarily limited"):
                mods.apply(self.profile, "canary", "vencord")
            with self.assertRaises(mods.RateLimited):
                mods.apply(self.profile, "canary", "equicord")
            self.assertEqual(run.call_count, 1)
            self.assertEqual(download.call_count, 1)
            self.assertEqual(mods.apply(self.profile, "canary", "none")["installed"], "none")
        with patch.object(mods.time, "time", return_value=1061):
            mods.installer_cooldown(self.profile / "dpe-updater")

    def test_success_exit_without_wrapper_still_reports_download_failure(self):
        with (
            patch.object(mods, "installer", return_value=Path("/fixture/installer")),
            patch.object(
                mods.subprocess,
                "run",
                return_value=mods.subprocess.CompletedProcess(
                    "fixture", 0, stdout=b"Failed to download renderer.js: unexpected EOF"
                ),
            ),
            self.assertRaisesRegex(ValueError, "download did not finish"),
        ):
            mods.apply(self.profile, "canary", "vencord")
        self.assertEqual((self.resources / "app.asar").read_bytes(), self.bootstrap)

    def test_failure_diagnostics_are_actionable_and_never_relay_arbitrary_output(self):
        cases = [
            (mods.subprocess.TimeoutExpired("secret path", 120), "two minutes"),
            (PermissionError("secret path"), "permissions"),
            (OSError(28, "secret path"), "free space"),
            (mods.subprocess.CalledProcessError(7, "fixture", output=b"secret path"), "code 7"),
        ]
        for error, expected in cases:
            with self.subTest(expected=expected):
                message = str(mods.installer_failure(self.profile / "dpe-updater", error))
                self.assertIn(expected, message)
                self.assertNotIn("secret", message)
                self.assertIn("current mod is unchanged", message)

    def test_cached_installer_hash_is_rechecked_and_valid_binary_never_downloaded(self):
        target = self.profile / "dpe-updater/mod-installers/" / mods.INSTALLERS["vencord"][1]
        target.parent.mkdir()
        target.write_bytes(b"fixture verified binary")
        with (
            patch.object(mods, "checksum", return_value=target.name),
            patch.object(mods, "download") as download,
        ):
            self.assertEqual(mods.installer(self.profile, "vencord"), target)
        download.assert_not_called()
        self.assertEqual(target.stat().st_mode & 0o777, 0o700)

    def test_installer_output_retains_only_bounded_tail_and_cleans_temporary_log(self):
        transcript = None

        def noisy(args, **kwargs):
            nonlocal transcript
            transcript = kwargs["stdout"]
            self.assertEqual(kwargs["timeout"], 120)
            transcript.write(b"x" * 32768 + b"download failed")
            raise mods.subprocess.CalledProcessError(1, args)

        with patch.object(mods.subprocess, "run", side_effect=noisy):
            with self.assertRaises(mods.subprocess.CalledProcessError) as caught:
                mods.run_installer(Path("/fixture"), self.profile, {})
        self.assertEqual(len(caught.exception.output), 16384)
        self.assertTrue(caught.exception.output.endswith(b"download failed"))
        self.assertTrue(transcript.closed)

    def test_payload_cache_rejects_a_redirected_parent_directory(self):
        directory = self.profile / "dpe-updater"
        with (
            patch.object(mods, "installer", return_value=Path("/fixture/installer")),
            patch.object(mods.subprocess, "run", side_effect=self.fake_install),
        ):
            mods.apply(self.profile, "canary", "vencord")
        self.assertIsNotNone(mods.cached_payload(directory, "vencord"))
        payloads = directory / "mod-payloads"
        moved = directory / "redirected-payloads"
        payloads.rename(moved)
        payloads.symlink_to(moved, target_is_directory=True)
        self.assertIsNone(mods.cached_payload(directory, "vencord"))


if __name__ == "__main__":
    unittest.main()
