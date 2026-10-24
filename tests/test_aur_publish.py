"""Metadata revisions are retryable without allowing a version's archive to change."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from publish_aur import publish, stage


class AurRevision(unittest.TestCase):
    def test_metadata_update_bumps_revision_once_and_refuses_changed_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)

            def git(*args):
                return subprocess.run(
                    ["git", *args], cwd=directory, check=True, capture_output=True
                )

            git("init", "-q")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.invalid")
            info = "pkgver = 20261024.120000\npkgrel = 1\npkgdesc = old\nsource = fixed-url\nsha256sums = original\n"
            (directory / ".SRCINFO").write_text(info)
            (directory / "PKGBUILD").write_text("pkgrel=1\npkgdesc=old\n")
            (directory / "discord.install").write_text("# fixture\n")
            git("add", ".")
            git("commit", "-qm", "initial")
            (directory / ".SRCINFO").write_text(info.replace("old", "new"))
            (directory / "PKGBUILD").write_text("pkgrel=1\npkgdesc=new\n")
            pushes = []

            def command(*args, cwd):
                if args[:2] == ("git", "push"):
                    pushes.append(args)
                else:
                    subprocess.run(args, cwd=cwd, check=True, capture_output=True)

            def srcinfo(path):
                self.assertIn("pkgrel=2", (path / "PKGBUILD").read_text())
                (path / ".SRCINFO").write_text(
                    info.replace("old", "new").replace("pkgrel = 1", "pkgrel = 2")
                )

            with (
                patch("publish_aur.run", side_effect=command),
                patch("publish_aur.write_srcinfo", side_effect=srcinfo),
            ):
                publish(directory, {"version": "20261024.120000"})
                self.assertEqual(len(pushes), 1)
                publish(directory, {"version": "20261024.120000"})
                self.assertEqual(len(pushes), 1)
                (directory / ".SRCINFO").write_text(info.replace("original", "replacement"))
                with self.assertRaisesRegex(ValueError, "archive contents"):
                    publish(directory, {"version": "20261024.120000"})
                self.assertEqual(len(pushes), 1)

    def test_staging_preserves_existing_revision_for_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / ".SRCINFO").write_text("pkgver = 20261024.120000\npkgrel = 3\n")
            manifest = {
                "version": "20261024.120000",
                "channel": "canary",
                "electron_version": "46.0.0-alpha.1",
            }
            with (
                patch("publish_aur.recipe", return_value="fixture") as recipe,
                patch("publish_aur.write_srcinfo"),
            ):
                stage(directory, manifest, "tag")
                recipe.assert_called_once_with(manifest, "tag", 3)
