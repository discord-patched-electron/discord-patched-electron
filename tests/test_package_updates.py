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
from publish_aur import recipe
from releases import release_tag


class PackageUpdates(unittest.TestCase):
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
