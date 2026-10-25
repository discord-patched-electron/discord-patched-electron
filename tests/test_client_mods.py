"""Loader detection must read ASAR structure and never evaluate JavaScript."""

import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mod_fixture import loader

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration/updater"))
from client_mods import MAX_LOADER_SIZE, detect


class ClientMods(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.resources = Path(temporary.name)
        self.archive = self.resources / "app.asar"

    def test_detects_both_official_loaders_and_preserves_exact_bytes(self):
        for name in ("Vencord", "Equicord"):
            with self.subTest(name=name):
                data = loader(name)
                self.archive.write_bytes(data)
                mod = detect(self.resources)
                self.assertEqual(mod.name, name)
                self.assertEqual(mod.loader, data)
                self.assertIn("Mod fixture", mod.target)

    def test_custom_unicode_and_quoted_paths_need_no_payload_execution(self):
        for name, target in (
            ("Vencord", '/custom/\u00e9 "dev"/dist/patcher.js'),
            ("Equicord", "/custom/\u00e9 build/desktop.asar"),
        ):
            with self.subTest(name=name):
                self.archive.write_bytes(loader(name, target=target))
                detected = detect(self.resources)
                self.assertEqual((detected.name, detected.target), (name, target))

    def test_rejects_plain_stale_unknown_and_truncated_archives(self):
        valid = loader()
        (self.resources / "_app.asar").write_bytes(valid)
        invalid = [
            b"project bootstrap",
            b'require("/tmp/Equicord/equicord.asar")',
            loader(target="relative/patcher.js"),
            loader(target="/tmp/other-mod.js"),
            loader(code='require("/tmp/patcher.js"); process.exit(1)'),
            loader(target="/tmp/\x00/patcher.js"),
            valid[:8],
            valid[:-1],
            struct.pack("<4I", 4, 0xFFFFFFFC, 0xFFFFFFF8, 1) + b"{}",
            b"x" * (MAX_LOADER_SIZE + 1),
        ]
        for data in invalid:
            with self.subTest(prefix=data[:32]):
                self.archive.write_bytes(data)
                self.assertIsNone(detect(self.resources))

    def test_missing_archive_or_link_is_not_an_installed_mod(self):
        self.assertIsNone(detect(self.resources))
        target = self.resources / "elsewhere.asar"
        target.write_bytes(loader())
        self.archive.symlink_to(target)
        self.assertIsNone(detect(self.resources))

    def test_read_failure_does_not_turn_an_install_into_an_uninstall(self):
        self.archive.write_bytes(loader())
        with patch.object(Path, "open", side_effect=PermissionError("unreadable loader")):
            with self.assertRaises(PermissionError):
                detect(self.resources)
