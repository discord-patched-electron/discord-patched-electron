"""Bug report archives collect evidence without account stores or linked files."""

import gzip
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration/updater"))
import export_logs


class ExportLogs(unittest.TestCase):
    def test_archive_logs_recent_crashes_limits_and_no_account_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "profile"
            for name in [
                "logs/renderer.log",
                "module_data/voice/logs/rtc.log",
                "1.2.3/chrome_debug.log",
                "settings.json",
                "Local Storage/secret.log",
            ]:
                target = profile / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(name)
            (profile / "logs/linked.log").symlink_to(profile / "settings.json")
            (profile / "logs/large.log").write_bytes(b"line\n" * 40)
            crashes = profile / "Crashpad/reports"
            crashes.mkdir(parents=True)
            for i in range(8):
                dump = crashes / f"{i}.dmp"
                dump.write_bytes(b"dump")
                os.utime(dump, (i + 1, i + 1))
            (crashes / "settings.dat").write_text("not a dump")
            output = root / "report.zip"
            with (
                patch.object(export_logs, "command", return_value="not available"),
                patch.object(export_logs, "LOG_LIMIT", 100),
            ):
                export_logs.collect(
                    profile,
                    [str(crashes.parent)],
                    output,
                    "/runtime/electron",
                    {"discord": "fixture"},
                )
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                self.assertIn("profile/logs/renderer.log", names)
                self.assertIn("profile/module_data/voice/logs/rtc.log", names)
                self.assertIn("profile/1.2.3/chrome_debug.log", names)
                self.assertEqual(sum(name.startswith("crashes/crash-") for name in names), 5)
                self.assertNotIn("crashes/0/reports/0.dmp", names)
                self.assertFalse(
                    any(
                        "secret" in name or "settings" in name or "linked" in name for name in names
                    )
                )
                self.assertEqual(archive.read("profile/logs/large.log"), b"line\n" * 20)
                report = json.loads(archive.read("manifest.json"))
                self.assertTrue(
                    any(item["reason"] == "Older log bytes omitted" for item in report["skipped"])
                )
                self.assertEqual(report["versions"]["discord"], "fixture")

    def test_system_core_and_journal_scoped_to_current_user_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            calls = []

            def command(args, output, limit=0):
                calls.append(args)
                output.write_text(
                    json.dumps([{"pid": 123, "timestamp": 42}])
                    if "list" in args
                    else "#0 0x1234 decode_video (libmedia.so + 0x1234)\nCommand Line: SECRET_COMMAND"
                    if "info" in args
                    else "evidence"
                )

            with patch.object(export_logs, "command", side_effect=command):
                export_logs.collect(root, [], root / "report.zip", "/runtime/electron", {})
            with zipfile.ZipFile(root / "report.zip") as archive:
                self.assertFalse(any("core-123" in name for name in archive.namelist()))
                self.assertEqual(
                    json.loads(archive.read("system/coredumps.json")),
                    [{"timestamp": 42, "stack": ["#0 0x1234 decode_video (libmedia.so + 0x1234)"]}],
                )
                self.assertEqual(archive.read("system/journal.log"), b"evidence")
            for args in calls:
                if args[0] == "coredumpctl" or any(arg.startswith("_EXE=") for arg in args):
                    self.assertTrue(any(arg.endswith("=/runtime/electron") for arg in args))
                    self.assertIn("--since=-7days", args)
                self.assertNotIn("dump", args)

    def test_redacts_entire_archive_and_excludes_opaque_payloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            logs = root / "logs"
            logs.mkdir()
            secrets = [
                "private@example.com",
                "192.168.10.20",
                "2001:db8::1234",
                "123456789012345678",
                "MY_SUPER_SECRET",
                "VeryPrivateMessage",
                "a" * 60,
                "SecretPerson",
            ]
            text = "\n".join(
                [
                    "email=private@example.com",
                    "peer 192.168.10.20 [2001:db8::1234]",
                    "channel 123456789012345678",
                    "Authorization: Bearer MY_SUPER_SECRET",
                    "content: VeryPrivateMessage",
                    "token=" + "a" * 60,
                    "username=SecretPerson",
                    "https://discord.com/api/webhooks/secret",
                    "/home/SecretPerson/private/file",
                    str(root / "private"),
                ]
            )
            (logs / "renderer.log").write_text(text)
            (logs / "private@example.com.log").write_text(text)
            (logs / "structured.log").write_text(
                json.dumps(
                    {
                        "password": "MY_SUPER_SECRET",
                        "nested": {"content": "VeryPrivateMessage", "userId": 123456789012345678},
                    }
                )
            )
            (logs / "events.log").write_text(
                json.dumps(
                    {
                        "details": {
                            "token": "MY_SUPER_SECRET",
                            "message": "Authorization: Bearer MY_SUPER_SECRET",
                        }
                    }
                )
                + "\n"
                + json.dumps({"content": "VeryPrivateMessage"})
                + "\n"
            )
            (logs / "voice.bin").write_bytes(b"\x00MY_SUPER_SECRET")
            (logs / "bad.log").write_bytes(b"\xffMY_SUPER_SECRET")
            (logs / "rotated.log.gz").write_bytes(gzip.compress(text.encode()))
            crashes = root / "Crashpad"
            crashes.mkdir()
            (crashes / "private@example.com.dmp").write_bytes(b"MDMP" + b"MY_SUPER_SECRET" * 10)
            archive = root / "report.zip"
            with patch.object(export_logs, "command", return_value="email private@example.com"):
                export_logs.collect(
                    root,
                    [str(crashes)],
                    archive,
                    "/runtime/electron",
                    {"email": secrets[0], "gpu": text},
                )
            with zipfile.ZipFile(archive) as source:
                all_text = (
                    "\n".join(source.namelist())
                    + "\n"
                    + "\n".join(source.read(name).decode() for name in source.namelist())
                )
                for secret in [*secrets, str(root), "api/webhooks/secret"]:
                    self.assertNotIn(secret, all_text)
                self.assertIn("profile/logs/rotated.log.gz.txt", source.namelist())
                self.assertNotIn("profile/logs/voice.bin", source.namelist())
                self.assertNotIn("profile/logs/bad.log", source.namelist())
                self.assertIn("crashes/crash-1.json", source.namelist())

    def test_missing_profile_and_bad_compression_do_not_abort_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(export_logs, "command", return_value="unavailable"):
                export_logs.collect(
                    root / "missing", [], root / "empty.zip", "/runtime/electron", {}
                )
                logs = root / "logs"
                logs.mkdir()
                (logs / "bad.log.gz").write_bytes(b"not gzip")
                export_logs.collect(root, [], root / "report.zip", "/runtime/electron", {})
            with zipfile.ZipFile(root / "report.zip") as source:
                self.assertIn("manifest.json", source.namelist())
                self.assertNotIn("profile/logs/bad.log.gz.txt", source.namelist())

    def test_deadline_keeps_a_usable_partial_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "logs").mkdir()
            (root / "logs/renderer.log").write_text("synthetic log")
            with (
                patch.object(export_logs.time, "monotonic", side_effect=[0, *([100] * 10)]),
                patch.object(export_logs, "command") as command,
            ):
                export_logs.collect(root, [], root / "report.zip", "/runtime/electron", {})
                command.assert_not_called()
            with zipfile.ZipFile(root / "report.zip") as source:
                manifest = json.loads(source.read("manifest.json"))
                self.assertTrue(
                    any(
                        item["reason"] == "Collection deadline reached"
                        for item in manifest["skipped"]
                    )
                )

    def test_subsystems_include_updater_state_published_modules_mods_and_service_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "profile"
            generation = "a" * 64
            files = {
                "logs/dpe/updater.log": json.dumps(
                    {"stage": "download", "label": "voice module", "token": "PRIVATE_TOKEN"}
                ),
                "logs/dpe/launcher-stderr.log": "native renderer crashed",
                "dpe-updater/current.json": json.dumps(generation),
                f"dpe-updater/generations/{generation}/client.json": json.dumps(
                    {"version": "1.2.3", "modules": {"voice": {"version": 5}}}
                ),
                "dpe-updater/activation.json": json.dumps({"error": "interrupted activation"}),
                "dpe-runtime/install/current.json": json.dumps(generation),
                f"dpe-runtime/install/generations/{generation}/runtime.json": json.dumps(
                    {"electron_version": "46.0.0"}
                ),
                "dpe-application/install/state.json": json.dumps(
                    {"current": generation, "pending": generation}
                ),
                "equicord/logs/main.log": "mod loading error",
            }
            for relative, content in files.items():
                file = profile / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(content)
            modules = root / "prepared/modules"
            (modules / "discord_utils").mkdir(parents=True)
            (modules / "discord_utils/discord_utils.log").write_text("native module failure")
            host = profile / "app-1.2.3"
            host.mkdir()
            (host / "modules").symlink_to(modules, target_is_directory=True)
            with sqlite3.connect(profile / "installer.db") as connection:
                connection.execute("CREATE TABLE key_values (key TEXT, value TEXT)")
                connection.executemany(
                    "INSERT INTO key_values VALUES (?,?)",
                    [
                        ("dpe/active/canary", json.dumps({"version": "1.2.3"})),
                        ("install_id", json.dumps("PRIVATE_INSTALLATION_ID")),
                        ("account", json.dumps("PRIVATE_ACCOUNT")),
                    ],
                )

            def command(args, destination, limit=0):
                if args[0] == "coredumpctl":
                    return "no crashes"
                destination.write_text(
                    "Server Name: PulseAudio\nDefault Sink: PRIVATE_DEVICE"
                    if args[0] == "pactl"
                    else "system evidence"
                )

            with patch.object(export_logs, "command", side_effect=command):
                export_logs.collect(
                    profile,
                    [],
                    root / "report.zip",
                    "/runtime/electron",
                    {"screensharing": {"active": False}},
                )
            with zipfile.ZipFile(root / "report.zip") as archive:
                names = archive.namelist()
                for name in (
                    "profile/logs/dpe/updater.log",
                    "modules/app-1.2.3/discord_utils/discord_utils.log",
                    "profile/equicord/logs/main.log",
                    "updater/state.json",
                    "system/hardware.json",
                    "runtime.json",
                    "coverage.json",
                    "system/media-services.log",
                    "system/services.log",
                ):
                    self.assertIn(name, names)
                state = json.loads(archive.read("updater/state.json"))["state"]
                self.assertEqual(state["client-generation"]["version"], "1.2.3")
                self.assertEqual(
                    state["installer-records"]["dpe/active/canary"]["version"], "1.2.3"
                )
                coverage = json.loads(archive.read("coverage.json"))
                self.assertIn(
                    "updater/state.json", coverage["application-client-runtime-updaters"]["files"]
                )
                all_text = "\n".join(archive.read(name).decode() for name in names)
                for secret in (
                    "PRIVATE_TOKEN",
                    "PRIVATE_INSTALLATION_ID",
                    "PRIVATE_ACCOUNT",
                    "PRIVATE_DEVICE",
                ):
                    self.assertNotIn(secret, all_text)

    def test_anonymous_generation_labels_preserve_equality_without_cross_export_tracking(self):
        redactor = export_logs.Redactor(Path("/profile"))
        current, pending = "a" * 64, "b" * 64
        first, second = redactor.text(current), redactor.text(pending)
        self.assertNotEqual(first, second)
        self.assertEqual(redactor.text(current), first)
        self.assertNotEqual(export_logs.Redactor(Path("/profile")).text(current), first)
        self.assertNotIn(current, first)
