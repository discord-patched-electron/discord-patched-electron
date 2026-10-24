"""An upgrade must not let a mod installer restore the previous Discord bootstrap."""

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import ROOT
from publish_aur import main as publish_aur
from publish_aur import recipe, require_approved
from releases import release_tag


class PackageUpdates(unittest.TestCase):
    def test_aur_requires_public_stable_release_and_exact_approval_record(self):
        release = {"draft": False, "prerelease": False, "tag_name": "tested-tag"}
        manifest = {"version": "20261024.120000"}
        approved = {
            "schema": 1,
            "tag": "tested-tag",
            "version": manifest["version"],
            "manifest_sha256": "a" * 64,
            "approved_by": "he3als",
        }
        require_approved(release, manifest, approved, "a" * 64)
        for change in ({"draft": True}, {"prerelease": True}, {"tag_name": "another-tag"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                require_approved({**release, **change}, manifest, approved, "a" * 64)
        for record in (
            None,
            {},
            {**approved, "manifest_sha256": "b" * 64},
            {**approved, "version": "20261023.120000"},
            {**approved, "approved_by": ""},
        ):
            with self.subTest(record=record), self.assertRaises(ValueError):
                require_approved(release, manifest, record, "a" * 64)

    def test_manual_aur_prerelease_is_rejected_before_download_or_ssh(self):
        for extra in ([], ["--verified-directory", "/unused"]):
            with (
                patch.object(sys, "argv", ["publish_aur.py", "test-tag", "--publish", *extra]),
                patch("publish_aur.api", return_value={"draft": False, "prerelease": True}),
                patch("publish_aur.approve") as download,
                patch("publish_aur.run") as command,
                patch("publish_aur.stage") as stage,
            ):
                with self.assertRaisesRegex(ValueError, "approved stable"):
                    publish_aur()
                download.assert_not_called()
                command.assert_not_called()
                stage.assert_not_called()

    def test_refreshes_stale_backup_but_leaves_installed_mod_and_clean_app_alone(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary)
            resources = app / "resources"
            resources.mkdir()
            bootstrap = resources / "app.asar"
            backup = resources / "_app.asar"
            bootstrap.write_bytes(b"new bootstrap")
            (app / "SHA256SUMS").write_text(
                hashlib.sha256(bootstrap.read_bytes()).hexdigest() + "  resources/app.asar\n"
            )
            command = ["sh", str(ROOT / "packaging/linux/refresh-mod-backup.sh"), str(app)]
            subprocess.run(command, check=True)
            self.assertFalse(backup.exists())
            backup.write_bytes(b"old bootstrap")
            subprocess.run(command, check=True)
            self.assertEqual(backup.read_bytes(), b"new bootstrap")
            bootstrap.write_bytes(b"installed mod loader")
            subprocess.run(command, check=True)
            self.assertEqual(backup.read_bytes(), b"new bootstrap")
            backup.unlink()
            backup.symlink_to(app / "outside")
            (app / "outside").write_bytes(b"unrelated")
            bootstrap.write_bytes(b"new bootstrap")
            subprocess.run(command, check=True)
            self.assertEqual((app / "outside").read_bytes(), b"unrelated")

    def test_aur_pins_portable_release_and_versions_download_cache(self):
        manifest = {
            "version": "20261024.120000",
            "channel": "canary",
            "electron_version": "46.0.0-alpha.1",
            "minimum_glibc": "2.38",
            "client_version": "1.0.2365",
            "release_key": "a" * 64,
            "assets": {
                "discord-canary-prerelease-patched-electron-bin-linux-x86_64.tar.gz": "b" * 64
            },
        }
        tag = release_tag(manifest["client_version"], manifest["release_key"])
        text = recipe(manifest, tag)
        self.assertIn("glibc>=2.38", text)
        self.assertIn(f"/releases/download/{tag}/", text)
        self.assertIn("$pkgname-$pkgver.tar.gz::", text)
        self.assertIn("options=('!strip' '!debug')", text)
        self.assertNotIn("releases/latest/", text)
        self.assertNotIn("SKIP", text)
        subprocess.run(["bash", "-n"], input=text, text=True, check=True)
        with self.assertRaisesRegex(ValueError, "identity"):
            recipe(manifest, "not-the-release")

    def test_cache_upload_never_uses_the_main_repository_token(self):
        import releases
        from common import digest
        from fetch_reference import CACHE_REPOSITORY

        runtime = {
            "cache_release": "runtime-cache-abc",
            "run": "123",
            "version": "46.0.0-alpha.1",
            "capacity_sha256": digest({}),
            "archive_sha256": "c" * 64,
        }
        with (
            patch("releases.api", return_value=None),
            patch("releases.fetch_runtime"),
            patch("releases.read_json", return_value={}),
            patch("releases.sha256", return_value="c" * 64),
            patch("releases.run") as command,
            patch.dict(os.environ, {"GH_TOKEN": "main-token", "CACHE_TOKEN": "cache-token"}),
        ):
            releases.cache_runtime({"runtime": runtime})
        self.assertEqual(command.call_count, 3)
        for call in command.call_args_list:
            self.assertIn(CACHE_REPOSITORY, call.args)
            self.assertEqual(call.kwargs["env"]["GH_TOKEN"], "cache-token")
