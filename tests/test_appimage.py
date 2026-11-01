"""AppImage wrapping preserves bootstrap identity and fails closed."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from appimage import build, stage
from common import sha256
from desktop_identity import desktop_id


class AppImages(unittest.TestCase):
    def test_wrapping_preserves_all_payload_bytes_and_channel_identity(self):
        for channel in ("canary", "ptb", "stable"):
            with self.subTest(channel=channel), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                app, appdir = root / "payload", root / "DPE.AppDir"
                app.mkdir()
                desktop = f"{desktop_id(channel)}.desktop"
                (app / desktop).write_text(
                    "[Desktop Entry]\nExec=/opt/old/launch.sh %U\nIcon=old\n"
                    "TryExec=/opt/old/launch.sh\nPath=/opt/old\n"
                )
                (app / "discord.png").write_bytes(b"icon")
                (app / "launch.sh").write_text("#!/bin/sh\nexit 0\n")
                before = {p.name: sha256(p) for p in app.iterdir()}
                stage(app, appdir, channel)
                self.assertEqual(before, {p.name: sha256(p) for p in app.iterdir()})
                self.assertEqual(
                    before, {p.name: sha256(p) for p in (appdir / "usr/lib/dpe").iterdir()}
                )
                self.assertEqual((appdir / ".DirIcon").read_bytes(), b"icon")
                text = (appdir / desktop).read_text()
                self.assertIn("Exec=AppRun %U", text)
                self.assertIn(f"Icon={desktop_id(channel)}", text)
                self.assertNotIn("/opt/old", text)

    def test_missing_builder_or_runtime_fails_instead_of_omitting_asset(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for installed in (False, True):
                if installed:
                    (root / "appimagetool").touch()
                with self.assertRaisesRegex(ValueError, "Install pinned"):
                    build(root / "app", root / "out.AppImage", 0, "canary", root)
                self.assertFalse((root / "out.AppImage").exists())
