"""Native installer compatibility, takeover rollback and writable-copy isolation."""

import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from mod_fixture import install, uninstall

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration/updater"))
import installation
from support import read_object, write_json


class NativeInstallation(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native install ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = self.root / "portable app"
        self.profile = self.root / "profile"
        self.host = self.root / "prepared"
        self.exe = self.app / "runtime/electron"
        for directory in (
            self.exe.parent,
            self.app / "resources",
            self.host / "resources",
            self.profile,
        ):
            directory.mkdir(parents=True)
        self.exe.write_text("fork runtime")
        (self.app / "resources/app.asar").write_text("project loader")
        (self.app / "libdpe-camera-probe.so").write_text("camera helper")
        self.state = {
            "channel": "canary",
            "version": "1.0.9",
            "modules": {"discord_voice": {"version": 7}},
        }
        write_json(self.host / "client.json", self.state)
        write_json(self.host / "resources/build_info.json", {"version": "1.0.9"})
        self.module = self.host / "modules/discord_voice/index.js"
        self.module.parent.mkdir(parents=True)
        self.module.write_text("prepared voice")

    def publish(self):
        return installation.publish(self.app, self.profile, self.host, self.exe)

    def database(self):
        with sqlite3.connect(self.profile / "installer.db") as connection:
            return {
                key: json.loads(value)
                for key, value in connection.execute("SELECT * FROM key_values")
            }

    def advance(self, *, version=None, revision=None):
        self.state["version"] = version or self.state["version"]
        if revision is not None:
            self.state["modules"]["discord_voice"]["version"] = revision
        destination = self.root / (
            f"prepared-{self.state['version']}-{self.state['modules']['discord_voice']['version']}"
        )
        shutil.copytree(self.host, destination)
        self.host = destination
        write_json(self.host / "client.json", self.state)
        write_json(self.host / "resources/build_info.json", {"version": self.state["version"]})

    def stock(self):
        public = self.profile / "app-1.0.9"
        public.mkdir()
        (public / "DiscordCanary").write_text("stock runtime")
        (self.profile / "DiscordCanary").symlink_to("app-1.0.9/DiscordCanary")
        with sqlite3.connect(self.profile / "installer.db") as connection:
            connection.execute(
                "CREATE TABLE key_values (key TEXT NOT NULL PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.executemany(
                "INSERT INTO key_values VALUES (?, ?)",
                [
                    ("install_id", '"existing-installation"'),
                    ("latest/host/app/canary/linux/x64", '{"untouched":true}'),
                    (
                        "host/app/canary/linux/x64",
                        json.dumps([{"host_version": {"version": [1, 0, 8]}}]),
                    ),
                ],
            )
        return public

    def test_native_layout_and_database_preserve_existing_installation(self):
        self.stock()
        public = self.publish()
        self.assertEqual(public, self.profile / "app-1.0.9")
        self.assertEqual(
            (public / "modules/discord_voice-7/discord_voice/index.js").read_text(),
            "prepared voice",
        )
        self.assertEqual((public / "electron").read_text(), "fork runtime")
        launcher = (self.profile / "DiscordCanary").read_text()
        self.assertIn("DPE_PROFILE_DIR=", launcher)
        self.assertIn("portable app/launch.sh'", launcher)
        rows = self.database()
        self.assertEqual(rows["install_id"], "existing-installation")
        self.assertEqual(rows["latest/host/app/canary/linux/x64"], {"untouched": True})
        hosts = rows["host/app/canary/linux/x64"]
        self.assertEqual(len(hosts), 2)
        self.assertEqual(hosts[-1]["host_version"]["version"], [1, 0, 9])
        self.assertEqual(hosts[-1]["modules"][0]["module_version"]["version"], 7)
        self.assertEqual(hosts[-1], read_object(public / "installer-record.json"))
        backup = next((self.profile / "dpe-updater/backups").iterdir())
        self.assertEqual((backup / "app-1.0.9/DiscordCanary").read_text(), "stock runtime")
        self.assertTrue((backup / "installer.db").is_file())
        self.assertFalse(public.is_symlink())
        target = public.resolve()
        self.assertEqual(self.publish().resolve(), target)
        self.assertEqual(self.database(), rows)

    def test_stock_writes_do_not_mutate_sources_and_are_repaired(self):
        public = self.publish()
        old = self.database()["dpe/active/canary"]["view"]
        (public / "electron").write_text("stock overwrite")
        (public / "modules/discord_voice-7/discord_voice/index.js").write_text("stock module")
        self.assertEqual(self.exe.read_text(), "fork runtime")
        self.assertEqual(self.module.read_text(), "prepared voice")
        restored = self.publish()
        self.assertNotEqual(self.database()["dpe/active/canary"]["view"], old)
        self.assertEqual((restored / "electron").read_text(), "fork runtime")
        backups = self.profile / "dpe-updater/backups"
        self.assertTrue(
            any(p.read_text() == "stock overwrite" for p in backups.glob("*/app-1.0.9/electron"))
        )

    def test_failed_activation_restores_stock_tree_links_and_database(self):
        stock = self.stock()
        before = self.database()
        original = installation.replace_link
        calls = 0

        def fail_second(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("simulated activation failure")
            return original(*args)

        with (
            patch.object(installation, "replace_link", side_effect=fail_second),
            self.assertRaises(OSError),
        ):
            self.publish()
        self.assertFalse(stock.is_symlink())
        self.assertEqual((self.profile / "DiscordCanary").read_text(), "stock runtime")
        self.assertEqual(self.database(), before)
        self.publish()
        self.assertEqual((self.profile / "app-1.0.9/electron").read_text(), "fork runtime")

    def test_same_path_runtime_or_mod_changes_refresh_view(self):
        self.publish()
        first = self.database()["dpe/active/canary"]["view"]
        self.exe.write_text("new fork runtime")
        self.publish()
        second = self.database()["dpe/active/canary"]["view"]
        self.assertNotEqual(first, second)
        (self.exe.parent / "libffmpeg.so").write_text("new runtime library")
        self.publish()
        third = self.database()["dpe/active/canary"]["view"]
        self.assertNotEqual(second, third)
        (self.app / "libdpe-camera-probe.so").write_text("updated helper")
        self.publish()
        fourth = self.database()["dpe/active/canary"]["view"]
        self.assertNotEqual(third, fourth)
        (self.app / "resources/mod.js").write_text("updated mod loader")
        self.publish()
        self.assertNotEqual(fourth, self.database()["dpe/active/canary"]["view"])

    def test_simultaneous_publishers_reuse_one_complete_view(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.publish().resolve(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(list((self.profile / "dpe-updater/installations").iterdir())), 1)
        self.assertEqual(len(self.database()["host/app/canary/linux/x64"]), 1)

    def test_unknown_database_records_fail_without_replacing_stock(self):
        stock = self.stock()
        with sqlite3.connect(self.profile / "installer.db") as connection:
            connection.execute("UPDATE key_values SET value='{}' WHERE key LIKE 'host/%'")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.publish()
        self.assertFalse(stock.is_symlink())
        self.assertEqual((self.profile / "DiscordCanary").read_text(), "stock runtime")

    def test_default_mod_loader_survives_update_and_uninstall(self):
        public = self.publish()
        resources = public / "resources"
        loader = install(resources)
        before = self.database()
        self.publish()
        self.assertEqual(self.database(), before)
        self.exe.write_text("updated fork")
        (self.app / "resources/app.asar").write_text("updated bootstrap")
        self.publish()
        self.assertEqual((resources / "app.asar").read_bytes(), loader)
        self.assertEqual((resources / "_app.asar").read_text(), "updated bootstrap")
        uninstall(resources)
        self.exe.write_text("another fork")
        self.publish()
        self.assertEqual((resources / "app.asar").read_text(), "updated bootstrap")
        self.assertFalse((resources / "_app.asar").exists())

    def test_both_mods_survive_module_runtime_package_and_host_updates(self):
        for name in ("Vencord", "Equicord"):
            with self.subTest(mod=name):
                self.profile = self.root / name
                self.state.update(version="1.0.9", modules={"discord_voice": {"version": 7}})
                write_json(self.host / "client.json", self.state)
                (self.app / "resources/app.asar").write_text("project loader")
                resources = self.publish() / "resources"
                loader = install(resources, name)
                # A normal launch must accept the loader without rebuilding.
                before = self.database()
                self.publish()
                self.assertEqual(self.database(), before)
                # Immutable module generations can change without a host bump.
                self.state["modules"]["discord_voice"]["version"] = 8
                write_json(self.host / "client.json", self.state)
                public = self.publish()
                self.assertTrue((public / "modules/discord_voice-8/discord_voice").is_dir())
                self.assertEqual((resources / "app.asar").read_bytes(), loader)
                self.exe.write_text(f"updated runtime for {name}")
                self.publish()
                self.assertEqual((resources / "app.asar").read_bytes(), loader)
                (self.app / "resources/app.asar").write_text("updated bootstrap")
                self.publish()
                self.assertEqual((resources / "app.asar").read_bytes(), loader)
                self.assertEqual((resources / "_app.asar").read_text(), "updated bootstrap")
                self.state["version"] = "1.0.10"
                write_json(self.host / "client.json", self.state)
                resources = self.publish() / "resources"
                self.assertEqual(resources.parent.name, "app-1.0.10")
                self.assertEqual((resources / "app.asar").read_bytes(), loader)
                self.assertEqual((resources / "_app.asar").read_text(), "updated bootstrap")
                self.assertTrue(installation.intact(resources.parent, self.state, allow_mod=True))
                uninstall(resources)
                # Previous host directories retain their mod; never import it again.
                self.state["version"] = "1.0.11"
                write_json(self.host / "client.json", self.state)
                resources = self.publish() / "resources"
                self.assertEqual((resources / "app.asar").read_text(), "updated bootstrap")
                self.assertFalse((resources / "_app.asar").exists())

    def test_host_update_keeps_mod_when_entry_is_missing_or_broken(self):
        for broken in (False, True):
            with self.subTest(broken=broken):
                self.profile = self.root / str(broken)
                self.state["version"] = "1.0.9"
                write_json(self.host / "client.json", self.state)
                loader = install(self.publish() / "resources", "Equicord")
                entry = self.profile / "DiscordCanary"
                entry.unlink()
                if broken:
                    entry.symlink_to("missing/DiscordCanary")
                self.state["version"] = "1.0.10"
                write_json(self.host / "client.json", self.state)
                resources = self.publish() / "resources"
                self.assertEqual((resources / "app.asar").read_bytes(), loader)
                self.assertEqual((resources / "_app.asar").read_text(), "project loader")

    def test_first_takeover_detects_stock_mod_with_no_launcher_link(self):
        self.stock()
        (self.profile / "DiscordCanary").unlink()
        for version, mod in (("1.0.9", "Vencord"), ("1.0.10", "Equicord")):
            resources = self.profile / f"app-{version}/resources"
            resources.mkdir(parents=True)
            (resources / "app.asar").write_text("stock bootstrap")
            expected = install(resources, mod)
        self.advance(version="1.0.11")
        resources = self.publish() / "resources"
        self.assertEqual((resources / "app.asar").read_bytes(), expected)
        self.assertEqual((resources / "_app.asar").read_text(), "project loader")

    def test_switching_mods_and_uninstall_survive_later_updates(self):
        resources = self.publish() / "resources"
        install(resources, "Vencord")
        self.exe.write_text("updated runtime")
        self.publish()
        uninstall(resources)
        expected = install(resources, "Equicord")
        self.advance(version="1.0.10")
        resources = self.publish() / "resources"
        self.assertEqual((resources / "app.asar").read_bytes(), expected)
        uninstall(resources)
        self.assertTrue(installation.intact(resources.parent, self.state))
        self.advance(version="1.0.11")
        resources = self.publish() / "resources"
        self.assertEqual((resources / "app.asar").read_text(), "project loader")
        self.assertFalse((resources / "_app.asar").exists())

    def test_package_mod_migrates_once_and_cannot_override_profile_uninstall(self):
        expected = install(self.app / "resources", "Vencord")
        resources = self.publish() / "resources"
        self.assertEqual((resources / "app.asar").read_bytes(), expected)
        self.assertEqual((resources / "_app.asar").read_text(), "project loader")
        uninstall(resources)
        self.exe.write_text("updated runtime")
        self.publish()
        self.assertEqual((resources / "app.asar").read_text(), "project loader")
        self.assertFalse((resources / "_app.asar").exists())

    def test_stale_backup_is_not_detected_as_a_mod_or_used_as_a_bootstrap(self):
        resources = self.publish() / "resources"
        (resources / "_app.asar").write_text("old bootstrap")
        (resources / "app.asar").write_text("stock overwrite")
        self.publish()
        self.assertEqual((resources / "app.asar").read_text(), "project loader")
        self.assertFalse((resources / "_app.asar").exists())

    def test_missing_or_corrupt_backup_is_repaired_without_removing_mod(self):
        resources = self.publish() / "resources"
        expected = install(resources, "Equicord")
        for corrupt in (None, b"stale bootstrap"):
            with self.subTest(corrupt=corrupt):
                backup = resources / "_app.asar"
                if corrupt is None:
                    backup.unlink()
                else:
                    backup.write_bytes(corrupt)
                self.publish()
                self.assertEqual((resources / "app.asar").read_bytes(), expected)
                self.assertEqual(backup.read_text(), "project loader")

    def test_failed_mod_update_rolls_back_and_retry_preserves_it(self):
        resources = self.publish() / "resources"
        expected = install(resources, "Vencord")
        before = self.database()
        (self.app / "resources/app.asar").write_text("updated bootstrap")
        with (
            patch.object(installation, "replace_link", side_effect=OSError("power loss")),
            self.assertRaises(OSError),
        ):
            self.publish()
        self.assertEqual(self.database(), before)
        self.assertEqual((resources / "app.asar").read_bytes(), expected)
        self.assertEqual((resources / "_app.asar").read_text(), "project loader")
        self.publish()
        self.assertEqual((resources / "app.asar").read_bytes(), expected)
        self.assertEqual((resources / "_app.asar").read_text(), "updated bootstrap")

    def test_abrupt_activation_interruption_recovers_on_next_launch(self):
        self.stock()
        before = self.database()
        # Simulate process death: exception rollback is deliberately unavailable.
        with (
            patch.object(installation, "replace_link", side_effect=OSError("power loss")),
            patch.object(installation, "recover"),
            self.assertRaises(OSError),
        ):
            self.publish()
        self.assertTrue((self.profile / "dpe-updater/activation.json").exists())
        with sqlite3.connect(self.profile / "installer.db") as connection:
            installation.recover(self.profile, connection)
        self.assertEqual((self.profile / "DiscordCanary").read_text(), "stock runtime")
        self.assertEqual(self.database(), before)
        self.publish()
        self.assertEqual((self.profile / "app-1.0.9/electron").read_text(), "fork runtime")

    def test_legacy_symlink_tree_migrates_without_losing_mod(self):
        view = installation.prepare_view(self.app, self.profile, self.host, self.exe, self.state)
        public = self.profile / "app-1.0.9"
        public.symlink_to(view)
        (self.profile / "DiscordCanary").symlink_to(public / "DiscordCanary")
        resources = public / "resources"
        loader = install(resources, "Equicord")
        self.publish()
        self.assertFalse(public.is_symlink())
        self.assertEqual((resources / "app.asar").read_bytes(), loader)
        self.assertEqual((resources / "_app.asar").read_text(), "project loader")

    def test_recovery_keeps_a_committed_activation(self):
        self.publish()
        active = self.database()["dpe/active/canary"]
        journal = self.profile / "dpe-updater/activation.json"
        write_json(journal, {"id": active["activation"], "key": "dpe/active/canary", "changes": []})
        with sqlite3.connect(self.profile / "installer.db") as connection:
            installation.recover(self.profile, connection)
        self.assertFalse(journal.exists())
        self.assertEqual((self.profile / "app-1.0.9/electron").read_text(), "fork runtime")

    def test_failed_public_copy_does_not_accumulate_partial_payloads(self):
        installation.prepare_view(self.app, self.profile, self.host, self.exe, self.state)
        with (
            patch.object(installation, "copy_payload", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            self.publish()
        self.assertFalse(list((self.profile / "dpe-updater/backups").glob("*/incoming")))
        self.assertFalse((self.profile / "app-1.0.9").exists())
