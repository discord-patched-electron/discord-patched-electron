"""Exercise signed GitHub updates with disposable keys and executable fixtures."""

import copy
import http.client
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integration/updater"))
import runtime
import support
from test_client_updater import client, manifest


def write_json(path, value):
    path.write_text(json.dumps(value))


@unittest.skipUnless(shutil.which("gpg") and shutil.which("gpgv"), "GnuPG required")
class RuntimeUpdates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory(prefix="runtime-signing-fixture-")
        cls.signer = ["gpg", "--batch", "--homedir", cls.keys.name]
        subprocess.run(
            [
                *cls.signer,
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
                "--quick-generate-key",
                "Disposable runtime test",
                "ed25519",
                "sign",
                "1d",
            ],
            check=True,
            capture_output=True,
        )
        cls.public = subprocess.check_output([*cls.signer, "--armor", "--export"])

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["gpgconf", "--homedir", cls.keys.name, "--kill", "gpg-agent"], check=True)
        cls.keys.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = self.root / "packaged app"
        (self.app / "resources").mkdir(parents=True)
        (self.app / "runtime").mkdir()
        (self.app / "resources/app.asar").write_bytes(b"existing mod loader")
        (self.app / "runtime/electron").write_bytes(b"bundled runtime stays unchanged")
        key = self.app / "integration/updater/release-signing-key.asc"
        key.parent.mkdir(parents=True)
        key.write_bytes(self.public)
        self.policy = {
            "schema": 1,
            "channel": "canary",
            "electron_channel": "prerelease",
            "electron_version": "45.0.0-beta.1",
            "runtime_key": "a" * 64,
            "archive_sha256": "b" * 64,
            "release_version": "20261024.120000",
        }
        write_json(self.app / "runtime-update.json", self.policy)
        write_json(self.app / "client-update.json", manifest())
        self.profile = self.root / "profile"
        self.cache = runtime.cache_root(self.profile, self.app)
        self.release = self.root / "release"
        self.release.mkdir()
        self.archive = self.release / "runtime-dist.zip"
        with zipfile.ZipFile(self.archive, "w") as bundle:
            for name in runtime.REQUIRED_FILES:
                data = b"fixture"
                if name == "electron":
                    data = b"#!/bin/sh\nprintf '%s\\n' '46.0.0-alpha.1'\n"
                bundle.writestr(name, data)
            bundle.writestr("resources/default_app.asar", b"upstream Electron demo")
        self.manifest = {
            "client": manifest(124),
            "client_version": "1.0.124",
            "source_dirty": False,
            "signing_required": True,
            "channel": "canary",
            "electron_version": "46.0.0-alpha.1",
            "version": "20261025.120000",
            "minimum_glibc": "2.1",
            "runtime": {
                "version": "46.0.0-alpha.1",
                "runtime_key": "c" * 64,
                "cache_release": "runtime-cache-" + "c" * 64,
                "archive_sha256": runtime.checksum(self.archive),
            },
        }
        self.tag = "v20261025.120000.0-canary.fixture"
        self.github = {
            "tag_name": self.tag,
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": name,
                    "browser_download_url": f"https://github.com/{runtime.REPOSITORY}/releases/download/{self.tag}/{name}",
                }
                for name in ("release.json", "SHA256SUMS", "SHA256SUMS.sig")
            ],
        }
        self.sign()
        self.downloads = []
        self.download_patch = patch.object(runtime, "download", side_effect=self.download)
        self.download_patch.start()
        self.addCleanup(self.download_patch.stop)
        self.environment = patch.dict(
            os.environ, {"DPE_RUNTIME_UPDATES": "1", "DPE_RUNTIME_BUNDLED": "0"}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def sign(self):
        write_json(self.release / "release.json", self.manifest)
        self.approval = {
            "schema": 1,
            "tag": self.tag,
            "version": self.manifest["version"],
            "manifest_sha256": runtime.checksum(self.release / "release.json"),
            "approved_by": "test-maintainer",
        }
        (self.release / "SHA256SUMS").write_text(
            runtime.checksum(self.release / "release.json") + "  release.json\n"
        )
        subprocess.run(
            [
                *self.signer,
                "--yes",
                "--detach-sign",
                "--output",
                str(self.release / "SHA256SUMS.sig"),
                str(self.release / "SHA256SUMS"),
            ],
            check=True,
            capture_output=True,
        )

    def test_damaged_runtime_files_are_downloaded_and_repaired(self):
        executable = runtime.ensure_runtime(self.app, self.profile)
        for name in ("electron", "resources.pak"):
            with self.subTest(name=name):
                (executable.parent / name).write_bytes(b"damaged")
                self.assertEqual(
                    runtime.ensure_runtime(self.app, self.profile, offline=True),
                    self.app / "runtime/electron",
                )
                (self.cache / "last-check.json").unlink()
                before = len(self.downloads)
                self.assertEqual(runtime.ensure_runtime(self.app, self.profile), executable)
                self.assertIn("runtime-dist.zip", self.downloads[-1])
                self.assertGreater(len(self.downloads), before)
                runtime.validate_generation(
                    self.cache, self.app, self.policy, executable.parent.name
                )
        self.assertEqual(len(list((self.cache / "generations").glob("*.damaged-*"))), 2)

    def download(self, url, target, **_options):
        self.downloads.append(url)
        if url == f"https://api.github.com/repos/{runtime.REPOSITORY}/releases/latest":
            write_json(target, self.github)
        elif (
            url
            == f"https://raw.githubusercontent.com/{runtime.REPOSITORY}/main/config/approved-release.json"
        ):
            write_json(target, self.approval)
        elif (
            url
            == f"https://github.com/{runtime.CACHE_REPOSITORY}/releases/download/runtime-cache-{'c' * 64}/runtime-dist.zip"
        ):
            shutil.copyfile(self.archive, target)
        else:
            prefix = f"https://github.com/{runtime.REPOSITORY}/releases/download/{self.tag}/"
            self.assertTrue(url.startswith(prefix), url)
            name = url.removeprefix(prefix)
            self.assertIn(name, ("release.json", "SHA256SUMS", "SHA256SUMS.sig"))
            shutil.copyfile(self.release / name, target)

    def clear_cooldown(self):
        (self.cache / "last-check.json").unlink(missing_ok=True)

    def test_signed_install_preserves_package_mods_and_reuses_cache(self):
        executable = runtime.ensure_runtime(self.app, self.profile)
        self.assertNotEqual(executable, self.app / "runtime/electron")
        self.assertTrue(executable.is_relative_to(self.cache))
        self.assertEqual((executable.parent / "resources").resolve(), self.app / "resources")
        self.assertEqual((self.app / "resources/app.asar").read_bytes(), b"existing mod loader")
        self.assertEqual(
            (self.app / "runtime/electron").read_bytes(), b"bundled runtime stays unchanged"
        )
        self.assertFalse((executable.parent / "resources/default_app.asar").exists())
        self.assertEqual(executable.stat().st_mode & 0o7777, 0o700)
        calls = len(self.downloads)
        self.assertEqual(runtime.ensure_runtime(self.app, self.profile), executable)
        self.assertEqual(len(self.downloads), calls)
        self.clear_cooldown()
        with patch.object(runtime, "download", side_effect=AssertionError("offline network")):
            self.assertEqual(
                runtime.ensure_runtime(self.app, self.profile, offline=True), executable
            )
        # Replacing the root-owned package with a newer runtime retires the overlay.
        write_json(
            self.app / "runtime-update.json", {**self.policy, "electron_version": "47.0.0-alpha.1"}
        )
        self.assertEqual(
            runtime.ensure_runtime(self.app, self.profile, offline=True),
            self.app / "runtime/electron",
        )

    def test_tampered_signature_manifest_or_archive_never_activates(self):
        for filename in ("SHA256SUMS.sig", "release.json", "runtime-dist.zip"):
            with self.subTest(filename=filename):
                target = self.release / filename
                original = target.read_bytes()
                target.write_bytes(original + b"tampered")
                # Appending to some signature formats is permitted; replacing it
                # with invalid bytes always exercises the signature rejection.
                if filename == "SHA256SUMS.sig":
                    target.write_bytes(b"not a signature")
                self.assertEqual(
                    runtime.ensure_runtime(self.app, self.profile), self.app / "runtime/electron"
                )
                self.assertFalse((self.cache / "current.json").exists())
                target.write_bytes(original)
                self.clear_cooldown()

    def test_network_and_probe_failures_preserve_the_complete_runtime(self):
        executable = runtime.ensure_runtime(self.app, self.profile)
        pointer = (self.cache / "current.json").read_bytes()
        self.clear_cooldown()
        with patch.object(runtime, "download", side_effect=OSError("offline")):
            self.assertEqual(runtime.ensure_runtime(self.app, self.profile), executable)
        self.assertEqual((self.cache / "current.json").read_bytes(), pointer)
        other_profile = self.root / "clean-profile"
        with patch.object(runtime, "probe", side_effect=ValueError("missing shared library")):
            self.assertEqual(
                runtime.ensure_runtime(self.app, other_profile), self.app / "runtime/electron"
            )
        self.assertFalse((runtime.cache_root(other_profile, self.app) / "current.json").exists())

    def test_wrong_channel_track_glibc_and_unapproved_releases_are_rejected(self):
        for change in (
            {"channel": "stable"},
            {"electron_version": "46.0.0"},
            {"minimum_glibc": "999.0"},
            {"source_dirty": True},
        ):
            with self.subTest(change=change):
                original = copy.deepcopy(self.manifest)
                self.manifest.update(change)
                self.sign()
                self.assertEqual(
                    runtime.ensure_runtime(self.app, self.profile), self.app / "runtime/electron"
                )
                self.assertFalse((self.cache / "current.json").exists())
                self.manifest = original
                self.clear_cooldown()
        self.sign()
        for field in ("draft", "prerelease"):
            self.github[field] = True
            self.assertEqual(
                runtime.ensure_runtime(self.app, self.profile), self.app / "runtime/electron"
            )
            self.github[field] = False
            self.clear_cooldown()

    def test_release_requires_the_exact_manual_approval_record(self):
        original = copy.deepcopy(self.approval)
        for change in (
            {"schema": 2},
            {"tag": "another-release"},
            {"version": "20261024.120000"},
            {"manifest_sha256": "0" * 64},
            {"approved_by": ""},
        ):
            with self.subTest(change=change):
                self.approval = {**original, **change}
                self.assertEqual(
                    runtime.ensure_runtime(self.app, self.profile), self.app / "runtime/electron"
                )
                self.assertFalse((self.cache / "current.json").exists())
                self.assertFalse(any(url.endswith("runtime-dist.zip") for url in self.downloads))
                self.clear_cooldown()
        self.approval = original
        self.assertNotEqual(
            runtime.ensure_runtime(self.app, self.profile), self.app / "runtime/electron"
        )

    @contextmanager
    def modules(self):
        """Exercise real approval verification, with synthetic module payloads."""
        downloaded = []

        def install(cache, name, source, compatibility, _executable):
            downloaded.append(source["url"])
            target = client.module_path(cache, name, source, compatibility)
            target.mkdir(parents=True)
            (target / "index.js").write_text(source["sha256"])
            write_json(
                target / ".complete",
                {"sha256": source["sha256"], "files": client.module_files(target)},
            )
            return target

        with (
            patch.object(client, "PACKAGE_ROOT", self.app),
            patch.object(client, "compatibility_key", return_value="f" * 64),
            patch.object(client, "install_module", side_effect=install),
            patch.dict(os.environ, {"DPE_CLIENT_PINNED": "0"}),
        ):
            yield self.profile / "dpe-updater", downloaded

    def test_client_and_asar_wait_for_approval_even_when_electron_does_not_change(self):
        # A desktop ASAR/native-module-only update, with the same host and runtime.
        self.manifest["client"] = manifest()
        self.manifest["client_version"] = "1.0.123"
        for name in ("discord_desktop_core", "discord_voice"):
            source = self.manifest["client"]["modules"][name]["full"]
            source.update(module_version=2, package_sha256="c" * 64)
            source["url"] = source["url"].replace("/1/full.distro", "/2/full.distro")
        self.manifest["electron_version"] = self.policy["electron_version"]
        self.manifest["runtime"].update(
            version=self.policy["electron_version"],
            runtime_key=self.policy["runtime_key"],
            archive_sha256=self.policy["archive_sha256"],
            cache_release="runtime-cache-" + self.policy["runtime_key"],
        )
        self.sign()
        with self.modules() as (root, downloaded):
            self.github["prerelease"] = True
            first = client.ensure_client(root, "canary")
            self.assertEqual(len(downloaded), len(client.REQUIRED))
            for approval in ({**self.approval, "manifest_sha256": "0" * 64}, self.approval):
                # A signed stable release with no matching approval remains blocked.
                self.github["prerelease"] = False
                self.approval = approval
                (root / "approvals/last-check.json").unlink()
                selected = client.ensure_client(root, "canary")
                if approval["manifest_sha256"] == "0" * 64:
                    self.assertEqual(selected, first)
                    self.assertEqual(len(downloaded), len(client.REQUIRED))
                else:
                    self.assertNotEqual(selected, first)
            self.assertEqual(sum("/2/full.distro" in url for url in downloaded), 2)
            self.assertFalse(any("updates.discord.com" in url for url in self.downloads))
            with patch.object(runtime, "download", side_effect=AssertionError("offline network")):
                self.assertEqual(client.ensure_client(root, "canary", offline=True), selected)

    def test_unapproved_legacy_cache_cannot_launch_offline_or_survive_online_repair(self):
        with self.modules() as (root, _downloaded):
            unapproved = {
                **client.normalize(manifest(125), "canary"),
                "approval": "package",
                "approved_version": "",
            }
            with patch.object(client, "approved_sources", return_value=[unapproved]):
                previous = client.ensure_client(root, "canary")
            with self.assertRaisesRegex(ValueError, "unapproved"):
                client.ensure_client(root, "canary", offline=True)
            approved = client.ensure_client(root, "canary")
            self.assertNotEqual(previous, approved)
            self.assertEqual(
                json.loads((approved / "client.json").read_text())["version"], "1.0.124"
            )
            self.assertTrue(previous.is_dir())

    def test_cached_approval_is_reverified_and_legacy_releases_do_not_authorize_latest(self):
        with self.modules() as (root, _downloaded):
            approved = client.ensure_client(root, "canary")
            state = json.loads((approved / "client.json").read_text())
            cached = root / "approvals" / state["approval"] / "release.json"
            cached.write_bytes(cached.read_bytes() + b"tampered")
            with self.assertRaisesRegex(ValueError, "unapproved"):
                client.ensure_client(root, "canary", offline=True)
            del self.manifest["client"]
            self.sign()
            (root / "approvals/last-check.json").unlink()
            restored = client.ensure_client(root, "canary")
            self.assertEqual(
                json.loads((restored / "client.json").read_text())["version"], "1.0.123"
            )
            self.assertFalse(any("updates.discord.com" in url for url in self.downloads))

    def test_package_testing_stays_on_its_exact_pins_without_release_lookups(self):
        with (
            self.modules() as (root, _downloaded),
            patch.dict(os.environ, {"DPE_CLIENT_PINNED": "1"}),
        ):
            selected = client.ensure_client(root, "canary")
            self.assertEqual(
                json.loads((selected / "client.json").read_text())["version"], "1.0.123"
            )
            self.assertEqual(self.downloads, [])

    def test_disabling_checks_and_using_bundled_runtime_are_independent(self):
        executable = runtime.ensure_runtime(self.app, self.profile)
        self.clear_cooldown()
        with (
            patch.dict(os.environ, {"DPE_RUNTIME_UPDATES": "0"}),
            patch.object(runtime, "download", side_effect=AssertionError("disabled network")),
        ):
            self.assertEqual(runtime.ensure_runtime(self.app, self.profile), executable)
        with patch.dict(os.environ, {"DPE_RUNTIME_BUNDLED": "1"}):
            self.assertEqual(
                runtime.ensure_runtime(self.app, self.profile), self.app / "runtime/electron"
            )
        executable.write_bytes(b"damaged runtime")
        self.assertEqual(
            runtime.ensure_runtime(self.app, self.profile, offline=True),
            self.app / "runtime/electron",
        )

    def test_interrupted_activation_reuses_verified_generation_without_redownloading(self):
        original_write = runtime.write_json

        def interrupted(path, value):
            if path.name == "current.json":
                raise OSError("interrupted pointer publication")
            original_write(path, value)

        with patch.object(runtime, "write_json", side_effect=interrupted):
            self.assertEqual(
                runtime.ensure_runtime(self.app, self.profile), self.app / "runtime/electron"
            )
        self.assertFalse((self.cache / "current.json").exists())
        self.clear_cooldown()
        self.downloads.clear()
        executable = runtime.ensure_runtime(self.app, self.profile)
        self.assertTrue(executable.is_relative_to(self.cache))
        self.assertFalse(any(url.endswith(".zip") for url in self.downloads))
        self.assertEqual(runtime.ensure_runtime(self.app, self.profile, offline=True), executable)

    def test_damaged_metadata_and_truncated_responses_do_not_disable_updates_or_launch(self):
        executable = runtime.ensure_runtime(self.app, self.profile)
        for damaged in ("{", "{}", "null", '"unexpected"'):
            (self.cache / "last-check.json").write_text(damaged)
            self.downloads.clear()
            self.assertEqual(runtime.ensure_runtime(self.app, self.profile), executable)
            self.assertTrue(self.downloads)
        for damaged in ("{", "{}", "null"):
            (self.cache / "current.json").write_text(damaged)
            self.clear_cooldown()
            self.assertEqual(runtime.ensure_runtime(self.app, self.profile), executable)
        self.clear_cooldown()
        with patch.object(
            runtime, "download", side_effect=http.client.IncompleteRead(b"truncated")
        ):
            self.assertEqual(runtime.ensure_runtime(self.app, self.profile), executable)
        (self.app / "runtime-update.json").write_text("[]")
        self.assertEqual(
            runtime.ensure_runtime(self.app, self.profile), self.app / "runtime/electron"
        )

    def test_missing_runtime_files_are_not_reused_offline(self):
        executable = runtime.ensure_runtime(self.app, self.profile)
        (executable.parent / "icudtl.dat").unlink()
        self.assertEqual(
            runtime.ensure_runtime(self.app, self.profile, offline=True),
            self.app / "runtime/electron",
        )


class RuntimeBoundaries(unittest.TestCase):
    def test_version_order_prevents_downgrades_but_accepts_newer_rebuilds(self):
        current = {
            "electron_version": "46.0.0-alpha.2",
            "archive_sha256": "a",
            "release_version": "20261025.120000",
        }
        for version, release, expected in (
            ("45.1.0", "20261026.120000", False),
            ("46.0.0-alpha.1", "20261026.120000", False),
            ("46.0.0-alpha.2", "20261024.120000", False),
            ("46.0.0-alpha.2", "20261026.120000", True),
            ("46.0.0-alpha.10", "20261026.120000", True),
            ("46.0.0-beta.1", "20261026.120000", True),
        ):
            self.assertEqual(
                runtime.newer(
                    {
                        "electron_version": version,
                        "release_version": release,
                        "archive_sha256": "b",
                    },
                    current,
                ),
                expected,
            )
        self.assertFalse(runtime.newer({**current, "release_version": "20261026.120000"}, current))

    def test_zip_paths_links_modules_and_device_files_are_rejected(self):
        for name, mode in (
            ("../escape", stat.S_IFREG),
            ("/absolute", stat.S_IFREG),
            ("link", stat.S_IFLNK),
            ("device", stat.S_IFCHR),
            ("discord_voice.node", stat.S_IFREG),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive = root / "runtime.zip"
                with zipfile.ZipFile(archive, "w") as bundle:
                    item = zipfile.ZipInfo(name)
                    item.external_attr = (mode | 0o777) << 16
                    bundle.writestr(item, b"unsafe")
                with self.assertRaises(ValueError):
                    runtime.extract(archive, root / "extracted")
                self.assertFalse((root / "escape").exists())

    def test_module_preparation_uses_the_selected_runtime(self):
        spec = importlib.util.spec_from_file_location(
            "check_updates", ROOT / "integration/updater/check.py"
        )
        check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check)
        with (
            patch.object(check, "ensure_runtime", return_value=Path("/selected/electron")),
            patch.object(check, "ensure_client", return_value=Path("/client/new")) as modules,
            patch.object(check, "publish", return_value=Path("/installed")) as publish,
            patch.object(
                check, "read_object", return_value={"modules": {"discord_voice": {"version": 7}}}
            ),
            patch.dict(os.environ),
        ):
            result = check.check(Path("/app"), Path("/profile/dpe-updater"), "canary")
            publish.assert_called_once_with(
                Path("/app"), Path("/profile"), Path("/client/new"), Path("/selected/electron")
            )
            self.assertEqual(
                result,
                {
                    "runtime": "/selected/electron",
                    "host": "/client/new",
                    "installed_runtime": "/installed/electron",
                    "voice": "/installed/modules/discord_voice-7/discord_voice",
                },
            )
            modules.assert_called_once_with(
                Path("/profile/dpe-updater"),
                "canary",
                offline=os.environ.get("DPE_CLIENT_OFFLINE") == "1",
                executable=Path("/selected/electron"),
            )

    def test_failed_metadata_publication_leaves_previous_pointer_and_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pointer = root / "current.json"
            support.write_json(pointer, "old")
            with patch.object(Path, "replace", side_effect=OSError("read-only")):
                with self.assertRaises(OSError):
                    support.write_json(pointer, "new")
            self.assertEqual(json.loads(pointer.read_text()), "old")
            self.assertEqual(list(root.iterdir()), [pointer])


if __name__ == "__main__":
    unittest.main()
