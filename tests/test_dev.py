"""Development storage and profile isolation contracts."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import read_json, write_json
from dev import Manager, copy_changed, lock, name


class DevTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.manager = Manager(Path(self.temporary.name) / "dev")

    def test_names_cannot_escape_managed_home(self):
        for value in ("../profile", "/tmp/profile", ".", "a/b", "", "a b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                name(value)

    def test_runtime_shares_bytes_but_resources_are_private(self):
        apps = [self.manager.path("versions", label) for label in ("one", "two")]
        for app in apps:
            (app / "runtime").mkdir(parents=True)
            (app / "resources").mkdir()
            (app / "resources/app.asar").write_bytes(b"private")
            (app / "runtime/electron").write_bytes(b"electron")
            (app / "runtime/electron").chmod(0o755)
            (app / "runtime/resources").symlink_to("../resources")
            self.manager.pool_runtime(app)
        first, second = [app / "runtime/electron" for app in apps]
        self.assertEqual(first.stat().st_ino, second.stat().st_ino)
        self.assertFalse(first.stat().st_mode & 0o222)
        self.assertNotEqual(
            (apps[0] / "resources/app.asar").stat().st_ino,
            (apps[1] / "resources/app.asar").stat().st_ino,
        )
        profile = self.manager.profile("dev")
        (profile / "settings.json").write_text("account stays here")
        self.manager.remove("versions", "one")
        self.assertTrue(second.exists())
        self.assertTrue((profile / "settings.json").exists())
        self.manager.prune(True, False)
        self.assertEqual(len(list((self.manager.home / "objects").iterdir())), 1)
        self.manager.remove("versions", "two")
        self.manager.prune(True, False)
        self.assertEqual(list((self.manager.home / "objects").iterdir()), [])

    def test_published_electron_files_share_pool_without_linking_resources(self):
        app = self.manager.path("versions", "one")
        (app / "runtime").mkdir(parents=True)
        (app / "runtime/electron").write_bytes(b"runtime")
        (app / "runtime/electron").chmod(0o755)
        self.manager.pool_runtime(app)
        profile = self.manager.profile("dev")
        views = (profile / "app-1.0.1", profile / "dpe-updater/installations/view")
        for view in views:
            view.mkdir(parents=True)
            (view / "electron").write_bytes(b"runtime")
            (view / "resources").mkdir()
            (view / "resources/app.asar").write_bytes(b"private")
        self.manager.share_installed_runtime(app, profile)
        for view in views:
            self.assertEqual(
                (view / "electron").stat().st_ino, (app / "runtime/electron").stat().st_ino
            )
            self.assertEqual((view / "resources/app.asar").stat().st_nlink, 1)

    def test_profile_lock_blocks_other_manager_and_cleanup(self):
        self.manager.profile("dev")
        with lock(self.manager.home / "locks/dev.profile"):
            with self.assertRaises(ValueError):
                self.manager.remove("profiles", "dev")
        self.manager.remove("profiles", "dev")

    def test_symlink_entry_refused(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (self.manager.home / "profiles/external").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.manager.remove("profiles", "external")
        self.assertTrue(outside.exists())

    def test_sync_replaces_links_without_mutating_shared_source(self):
        root = Path(self.temporary.name)
        source, destination, other = root / "source", root / "destination", root / "other"
        source.write_text("new")
        other.write_text("old")
        destination.symlink_to(other)
        copy_changed(source, destination)
        self.assertEqual(other.read_text(), "old")
        self.assertEqual(destination.read_text(), "new")
        before = destination.stat().st_mtime_ns
        copy_changed(source, destination)
        self.assertEqual(destination.stat().st_mtime_ns, before)

    def test_create_failure_cleans_partial_version(self):
        with self.assertRaises((OSError, ValueError)):
            self.manager.create("failed", Path("/missing"), Path("/missing"))
        self.assertFalse(self.manager.path("versions", "failed").exists())

    def test_development_identity_tracks_links_and_keeps_native_content_hashes(self):
        app = self.manager.path("versions", "fixture")
        updater = app / "integration/updater"
        updater.mkdir(parents=True)
        (updater / "client.py").write_text("def compatibility_key():\n    return 'native-base'\n")
        installation = updater / "installation.py"
        installation.write_text(
            "import os, hashlib\nfrom pathlib import Path\n"
            "def checksum(item): return hashlib.sha256(item.read_bytes()).hexdigest()\n"
            "def manifest(directory):\n    files = {}\n"
            "    for item in directory.iterdir():\n"
            "        relative = item.name\n        if item.is_file():\n"
            '            files[relative] = {"New": {"Sha256": checksum(item)}}\n'
            "    return files\n"
        )
        self.manager.freeze_client(app)
        namespace = {}
        exec(installation.read_text(), namespace)
        data = app / "data"
        data.mkdir()
        helper = app / "helper"
        helper.write_text("old")
        (data / "helper").symlink_to(helper)
        native = data / "native.node"
        native.write_bytes(b"native")
        before = namespace["manifest"](data)
        helper.write_text("edited")
        self.assertEqual(namespace["manifest"](data), before)
        native.write_bytes(b"changed native")
        self.assertNotEqual(namespace["manifest"](data), before)
        result = subprocess.check_output(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "import client; print(client.compatibility_key())",
                str(updater),
            ],
            text=True,
        )
        self.assertEqual(result.strip(), "native-base")

    def test_run_forwards_flags_and_profile(self):
        app = self.manager.path("versions", "one")
        app.mkdir()
        write_json(app / "dev-version.json", {"schema": 1})
        # Exercise argument parsing independently: unknown Electron flags are
        # only accepted after the explicit separator.
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "scripts/dev.py"),
                "--home",
                str(self.manager.home),
                "run",
                "missing",
                "--profile",
                "other",
                "--",
                "--ozone-platform=x11",
            ],
            capture_output=True,
            text=True,
        )
        self.assertIn("Unknown version", result.stderr)
        self.assertEqual(read_json(app / "dev-version.json")["schema"], 1)


if __name__ == "__main__":
    unittest.main()
