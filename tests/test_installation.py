"""Native installer compatibility, takeover rollback and writable-copy isolation."""

import json
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

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
        (resources / "app.asar").rename(resources / "_app.asar")
        (resources / "app.asar").write_text("user mod loader")
        before = self.database()
        self.publish()
        self.assertEqual(self.database(), before)
        self.exe.write_text("updated fork")
        (self.app / "resources/app.asar").write_text("updated bootstrap")
        self.publish()
        self.assertEqual((resources / "app.asar").read_text(), "user mod loader")
        self.assertEqual((resources / "_app.asar").read_text(), "updated bootstrap")
        (resources / "app.asar").unlink()
        (resources / "_app.asar").rename(resources / "app.asar")
        self.exe.write_text("another fork")
        self.publish()
        self.assertEqual((resources / "app.asar").read_text(), "updated bootstrap")
        self.assertFalse((resources / "_app.asar").exists())

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
        (resources / "app.asar").rename(resources / "_app.asar")
        (resources / "app.asar").write_text("legacy mod")
        self.publish()
        self.assertFalse(public.is_symlink())
        self.assertEqual((resources / "app.asar").read_text(), "legacy mod")
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
