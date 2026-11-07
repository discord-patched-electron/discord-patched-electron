"""Metadata revisions are retryable without allowing a version's archive to change."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from publish_aur import publish, stage


class AurRevision(unittest.TestCase):
    def test_bootstrap_release_retains_removal_hook_without_legacy_install_actions(self):
        manifest = {
            "version": "20261107.120000",
            "channel": "canary",
            "electron_version": "46.0.0-alpha.1",
            "package_distribution": "bootstrap-only",
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with (
                patch("publish_aur.recipe", return_value="fixture"),
                patch("publish_aur.write_srcinfo"),
            ):
                stage(directory, manifest, "tag")
            hook = (directory / "discord.install").read_text()
            self.assertIn("pre_remove()", hook)
            self.assertIn("/opt/discord-canary-prerelease-patched-electron-bin/uninstall.py", hook)
            self.assertNotIn("post_install", hook)
            self.assertNotIn("post_upgrade", hook)
            subprocess.run(["bash", "-n", str(directory / "discord.install")], check=True)
            # Exercise the published hook against an isolated installation,
            # including an old immutable archive without the new uninstaller.
            installed = directory / "installed"
            installed.mkdir()
            hook_path = directory / "hook.install"
            hook_path.write_text(
                hook.replace("/opt/discord-canary-prerelease-patched-electron-bin", str(installed))
            )
            fake = directory / "python3"
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$DPE_UNINSTALL_LOG"\n')
            fake.chmod(0o755)
            log = directory / "called"
            environment = {
                **os.environ,
                "PATH": f"{directory}:/usr/bin",
                "DPE_UNINSTALL_LOG": str(log),
            }
            command = [
                "bash",
                "-c",
                'source "$1"; pre_remove fixture-version',
                "fixture",
                str(hook_path),
            ]
            subprocess.run(command, env=environment, check=True)
            self.assertFalse(log.exists())
            (installed / "uninstall.py").touch()
            subprocess.run(command, env=environment, check=True)
            self.assertEqual(
                log.read_text().splitlines(),
                [
                    "-I",
                    str(installed / "uninstall.py"),
                    "--package",
                    "discord-canary-prerelease-patched-electron-bin",
                ],
            )

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
