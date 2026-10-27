"""Exercise module-only updates, atomic activation and archive boundaries."""

import copy
import http.client
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.request
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "integration/updater"))
from check_release_packages import check_client_free_archive
from common import sha256
from stage_updater import stage, validate_distribution

spec = importlib.util.spec_from_file_location(
    "client_updater", ROOT / "integration/updater/client.py"
)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def manifest(version=123):
    result = {
        "full": {
            "host_version": [1, 0, version],
            "url": "https://example.invalid/NEVER-DOWNLOAD-HOST",
        },
        "modules": {},
        "required_modules": sorted(client.REQUIRED),
    }
    for name in sorted(client.REQUIRED):
        result["modules"][name] = {
            "full": {
                "host_version": [1, 0, version],
                "module_version": 1,
                "package_sha256": "a" * 64,
                "url": f"https://canary.dl2.discordapp.net/distro/app/canary/linux/x64/1.0.{version}/{name}/1/full.distro",
            }
        }
    return result


class ModuleUpdates(unittest.TestCase):
    def setUp(self):
        # These tests exercise installation after policy validation. Real signed
        # approvals and their cache are exercised in test_runtime_updater.py.
        self.release = Mock(return_value=client.normalize(manifest(), "canary"))
        self.approved = []

        def sources(_root, _channel, *, offline, previous):
            if not offline:
                try:
                    state = {**self.release(), "approval": "package", "approved_version": ""}
                    self.approved.append(copy.deepcopy(state))
                except (OSError, http.client.HTTPException):
                    if not self.approved:
                        raise
            return self.approved

        resolver = patch.object(client, "approved_sources", side_effect=sources)
        resolver.start()
        self.addCleanup(resolver.stop)

    def test_manifest_has_no_host_download_and_validates_sources(self):
        source = manifest()
        normalized = client.normalize(source, "canary")
        self.assertNotIn("NEVER-DOWNLOAD-HOST", json.dumps(normalized))
        for url in (
            "http://canary.dl2.discordapp.net/evil",
            "https://other.invalid/evil",
            source["modules"]["discord_voice"]["full"]["url"] + "?x=1",
        ):
            invalid = copy.deepcopy(source)
            invalid["modules"]["discord_voice"]["full"]["url"] = url
            with self.assertRaises(ValueError):
                client.normalize(invalid, "canary")
        del source["modules"]["discord_voice"]
        with self.assertRaisesRegex(ValueError, "required"):
            client.normalize(source, "canary")

    def test_changed_module_commits_together_and_failure_keeps_previous(self):
        downloaded = []

        def fetch(url, target, expected):
            downloaded.append(url)
            self.assertNotIn("NEVER-DOWNLOAD-HOST", url)
            self.assertRegex(url, r"/discord_[a-z_]+/1/full.distro$")
            target.write_bytes(b"fixture")

        def extract(_archive, destination):
            (destination / "index.js").write_text("module.exports = {}")

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(client, "fetch", side_effect=fetch),
            patch.object(client, "extract_module", side_effect=extract),
            patch.object(client.subprocess, "run"),
            patch.object(client, "compatibility_key", return_value="b" * 64),
        ):
            root = Path(temporary)
            with patch.object(self, "release", return_value=client.normalize(manifest(), "canary")):
                first = client.ensure_client(root, "canary")
                self.assertEqual(client.ensure_client(root, "canary"), first)
            self.assertEqual(len(downloaded), len(client.REQUIRED))
            pointer = (root / "current.json").read_bytes()
            updated = manifest(124)
            updated["modules"]["discord_voice"]["full"]["package_sha256"] = "c" * 64
            with (
                patch.object(self, "release", return_value=client.normalize(updated, "canary")),
                patch.object(client, "fetch", side_effect=OSError("offline")),
            ):
                self.assertEqual(client.ensure_client(root, "canary"), first)
            self.assertEqual((root / "current.json").read_bytes(), pointer)
            with patch.object(self, "release", return_value=client.normalize(updated, "canary")):
                second = client.ensure_client(root, "canary")
            self.assertNotEqual(second, first)
            self.assertEqual(len(downloaded), len(client.REQUIRED) + 1)
            self.assertEqual(client.current_host(root, "canary"), second)
            with patch.object(
                self, "release", side_effect=AssertionError("offline must not use network")
            ):
                self.assertEqual(client.ensure_client(root, "canary", offline=True), second)

    def test_first_install_failure_does_not_publish_pointer(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(self, "release", side_effect=OSError("offline")),
        ):
            root = Path(temporary)
            with self.assertRaises(OSError):
                client.ensure_client(root, "canary")
            self.assertFalse((root / "current.json").exists())
            with self.assertRaisesRegex(ValueError, "internet"):
                client.ensure_client(root, "canary", offline=True)

    def test_damaged_pointer_recovers_and_same_host_module_downgrades_are_rejected(self):
        state = client.normalize(manifest(), "canary")
        state["modules"]["discord_voice"]["version"] = 2

        def extract(_archive, destination):
            (destination / "index.js").write_text("module.exports = {}")

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(client, "fetch") as fetch,
            patch.object(client, "extract_module", side_effect=extract),
            patch.object(client.subprocess, "run"),
            patch.object(client, "compatibility_key", return_value="b" * 64),
            patch.object(self, "release", return_value=state) as latest,
        ):
            root = Path(temporary)
            previous = client.ensure_client(root, "canary")
            calls = fetch.call_count
            for damaged in ("invalid-json", "null", "{}", '"' + "c" * 64 + '"'):
                (root / "current.json").write_text(damaged)
                self.assertEqual(client.ensure_client(root, "canary"), previous)
                self.assertEqual(client.current_host(root, "canary"), previous)
            self.assertEqual(fetch.call_count, calls)
            latest.return_value = client.normalize(manifest(), "canary")
            self.assertEqual(client.ensure_client(root, "canary"), previous)
            self.assertEqual(fetch.call_count, calls)
            latest.side_effect = http.client.IncompleteRead(b"truncated")
            self.assertEqual(client.ensure_client(root, "canary"), previous)

    def test_malformed_manifest_objects_fail_with_a_validation_error(self):
        for value in (None, [], {**manifest(), "modules": []}, {**manifest(), "full": None}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client.normalize(value, "canary")

    def test_damaged_cached_files_markers_and_generation_metadata_can_be_repaired(self):
        def extract(_archive, destination):
            (destination / "index.js").write_text("entry point")
            (destination / "payload.node").write_text("verified native payload")

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(client, "fetch") as fetch,
            patch.object(client, "extract_module", side_effect=extract),
            patch.object(client.subprocess, "run"),
            patch.object(client, "compatibility_key", return_value="b" * 64),
            patch.object(self, "release", return_value=client.normalize(manifest(), "canary")),
        ):
            root = Path(temporary)
            host = client.ensure_client(root, "canary")
            module = (host / "modules/discord_voice").resolve()
            for name in ("index.js", "payload.node", ".complete"):
                with self.subTest(name=name):
                    (module / name).write_text("corrupt")
                    with self.assertRaisesRegex(ValueError, "incomplete"):
                        client.ensure_client(root, "canary", offline=True)
                    calls = fetch.call_count
                    self.assertEqual(client.ensure_client(root, "canary"), host)
                    self.assertEqual(fetch.call_count, calls + 1)
                    self.assertEqual(
                        (module / "payload.node").read_text(), "verified native payload"
                    )
            (host / "resources/build_info.json").unlink()
            self.assertEqual(client.ensure_client(root, "canary"), host)
            self.assertTrue((host / "resources/build_info.json").is_file())
            self.assertTrue(list((root / "cache").glob("*.damaged-*")))
            self.assertTrue(list((root / "generations").glob("*.damaged-*")))
            self.assertEqual(client.current_host(root, "canary"), host)

    def test_missing_update_dependencies_do_not_block_a_complete_cached_client(self):
        def extract(_archive, destination):
            (destination / "index.js").write_text("entry point")

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(client, "fetch"),
            patch.object(client, "extract_module", side_effect=extract),
            patch.object(client.subprocess, "run"),
            patch.object(client, "compatibility_key", return_value="b" * 64),
            patch.object(
                self, "release", return_value=client.normalize(manifest(), "canary")
            ) as latest,
        ):
            root = Path(temporary)
            host = client.ensure_client(root, "canary")
            with patch.object(
                client, "prerequisites", side_effect=ValueError("Brotli is missing")
            ) as prerequisites:
                self.assertEqual(client.ensure_client(root, "canary"), host)
                prerequisites.assert_not_called()
                updated = client.normalize(manifest(124), "canary")
                updated["modules"]["discord_voice"]["sha256"] = "c" * 64
                latest.return_value = updated
                self.assertEqual(client.ensure_client(root, "canary"), host)
                prerequisites.assert_called_once()

    def test_archives_reject_traversal_external_links_and_devices(self):
        for name, kind, link in (
            ("files/../../outside", tarfile.REGTYPE, ""),
            ("files/link", tarfile.SYMTYPE, "../../outside"),
            ("files/device", tarfile.CHRTYPE, ""),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                raw = io.BytesIO()
                with tarfile.open(fileobj=raw, mode="w") as archive:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = link
                    archive.addfile(member)
                compressed = root / "module.br"
                compressed.write_bytes(
                    subprocess.check_output(["brotli", "--stdout"], input=raw.getvalue())
                )
                destination = root / "files"
                destination.mkdir()
                with self.assertRaisesRegex(ValueError, "Unsafe"):
                    client.extract_module(compressed, destination)
                self.assertFalse((root / "outside").exists())

    def test_failed_desktop_preflight_keeps_previous_generation(self):
        def extract(_archive, destination):
            (destination / "index.js").write_text("module.exports = {}")

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(client, "fetch"),
            patch.object(client, "extract_module", side_effect=extract),
            patch.object(client, "compatibility_key", return_value="b" * 64),
            patch.object(client.subprocess, "run") as run,
        ):
            root = Path(temporary)
            with patch.object(self, "release", return_value=client.normalize(manifest(), "canary")):
                previous = client.ensure_client(root, "canary")
            commands = [call.args[0] for call in run.call_args_list]
            self.assertTrue(any(command[1].endswith("patches.cjs") for command in commands))
            self.assertTrue(all(call.kwargs["stdout"] is sys.stderr for call in run.call_args_list))
            updated = client.normalize(manifest(124), "canary")
            updated["modules"]["discord_desktop_core"]["sha256"] = "d" * 64
            run.side_effect = subprocess.CalledProcessError(1, "desktop-preflight")
            with patch.object(self, "release", return_value=updated):
                self.assertEqual(client.ensure_client(root, "canary"), previous)
            self.assertEqual(client.current_host(root, "canary"), previous)
            failed = client.module_path(
                root / "cache",
                "discord_desktop_core",
                updated["modules"]["discord_desktop_core"],
                "b" * 64,
            )
            self.assertFalse(failed.exists())

    def test_checksum_mismatch_and_oversized_download_are_rejected(self):
        class Opener:
            def open(self, *_args, **_kwargs):
                response = io.BytesIO(b"incorrect bytes")
                response.headers = Message()
                return response

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(urllib.request, "build_opener", return_value=Opener()),
        ):
            archive = Path(temporary) / "module.br"
            with self.assertRaisesRegex(ValueError, "checksum"):
                client.fetch("https://canary.dl2.discordapp.net/test", archive, "a" * 64)
            with self.assertRaisesRegex(ValueError, "size limit"):
                client.fetch("https://canary.dl2.discordapp.net/test", archive, "a" * 64, limit=2)

    def test_short_download_is_rejected_even_without_a_progress_label(self):
        class Opener:
            def open(self, *_args, **_kwargs):
                response = io.BytesIO(b"short")
                response.headers = Message()
                response.headers["Content-Length"] = "100"
                return response

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(urllib.request, "build_opener", return_value=Opener()),
        ):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                client.download("https://example.invalid/metadata", Path(temporary) / "download")

    def test_internal_executable_symlinks_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = io.BytesIO()
            with tarfile.open(fileobj=raw, mode="w") as archive:
                member = tarfile.TarInfo("files/node_modules/.bin/tool")
                member.type = tarfile.SYMTYPE
                member.linkname = "../tool/index.js"
                archive.addfile(member)
            compressed = root / "module.br"
            compressed.write_bytes(
                subprocess.check_output(["brotli", "--stdout"], input=raw.getvalue())
            )
            client.extract_module(compressed, root)
            self.assertEqual((root / "node_modules/.bin/tool").readlink(), Path("../tool/index.js"))

    def test_extraction_limit_includes_trailing_decompressed_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compressed = root / "module.br"
            # An empty tar ends after two zero blocks; data after it still costs
            # decompression work and must count towards the extraction limit.
            compressed.write_bytes(
                subprocess.check_output(["brotli", "--stdout"], input=b"\0" * (32 * 1024))
            )
            with self.assertRaisesRegex(ValueError, "size limit"):
                client.extract_module(compressed, root / "files", limit=16 * 1024)


class DistributionBoundary(unittest.TestCase):
    def test_archives_reject_old_client_directories_and_unexpected_links(self):
        for name, kind, link in (
            ("app-1.0.123", tarfile.DIRTYPE, ""),
            ("scripts/__pycache__", tarfile.DIRTYPE, ""),
            ("resources/client", tarfile.SYMTYPE, "../../private-client"),
            ("modules", tarfile.DIRTYPE, ""),
        ):
            with self.subTest(name=name):
                data = io.BytesIO()
                with tarfile.open(fileobj=data, mode="w") as bundle:
                    for filename, value in {
                        "client-distribution.json": json.dumps(
                            {
                                "mode": "module-only",
                                "bundled_discord_client": False,
                                "loader_sha256": client.hashlib.sha256(b"loader").hexdigest(),
                            }
                        ).encode(),
                        "resources/app.asar": b"loader",
                    }.items():
                        entry = tarfile.TarInfo("app/" + filename)
                        entry.size = len(value)
                        bundle.addfile(entry, io.BytesIO(value))
                    entry = tarfile.TarInfo("app/" + name)
                    entry.type, entry.linkname = kind, link
                    bundle.addfile(entry)
                data.seek(0)
                with tarfile.open(fileobj=data) as bundle, self.assertRaises(ValueError):
                    check_client_free_archive(bundle, "app")

    def test_stages_only_our_loader_and_pinned_bootstrap_source(self):
        asar = str(ROOT / "node_modules/.bin/asar")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "source.tar.gz"
            commit = "a" * 40
            with tarfile.open(archive, "w:gz") as source:
                for name, value in {
                    "src/index.js": b"// bootstrap source fixture\n",
                    "LICENSE": b"license fixture\n",
                }.items():
                    member = tarfile.TarInfo(f"OpenAsar-{commit}/{name}")
                    member.size = len(value)
                    source.addfile(member, io.BytesIO(value))
            lock = {"commit": commit, "sha256": sha256(archive)}
            app = root / "app"
            locked_client = {
                "channel": "canary",
                "version": "1.0.123",
                "required_modules": sorted(client.REQUIRED),
                "sources": {
                    name: {"url": entry["full"]["url"], "sha256": "a" * 64, "module_version": 1}
                    for name, entry in manifest()["modules"].items()
                },
            }
            with (
                patch("stage_updater.read_json", return_value=lock),
                patch("stage_updater.stage_python"),
                patch("stage_updater.stage_editor"),
            ):
                stage(app, "canary", "1.0.123", asar, archive, client=locked_client)
            validate_distribution(app)
            pinned = client.normalize(
                json.loads((app / "client-update.json").read_text()), "canary"
            )
            self.assertEqual(pinned, client.normalize(manifest(), "canary"))
            self.assertNotIn("NEVER-DOWNLOAD-HOST", (app / "client-update.json").read_text())
            unpacked = root / "unpacked"
            subprocess.run(
                [asar, "extract", str(app / "resources/app.asar"), str(unpacked)], check=True
            )
            self.assertEqual({p.name for p in unpacked.iterdir()}, {"package.json", "main.cjs"})
            self.assertEqual(
                json.loads((unpacked / "package.json").read_text())["desktopName"],
                "discord-canary.desktop",
            )
            self.assertEqual(
                (app / "discord.png").read_bytes(),
                (ROOT / "discord-patched-electron.png").read_bytes(),
            )
            self.assertEqual(
                (unpacked / "main.cjs").read_text(),
                'require(process.env.DPE_APP_ROOT + "/integration/updater/entry.cjs");\n',
            )
            self.assertEqual(
                (app / "bootstrap/src/index.js").read_bytes(), b"// bootstrap source fixture\n"
            )
            for name in (
                "resources/build_info.json",
                "openasar-source.json",
                "client-distribution.json",
                "scripts/patch_pulse_audio.py",
                "client-update.json",
            ):
                self.assertEqual((app / name).stat().st_mode & 0o444, 0o444)
            with patch("stage_updater.read_json", return_value={**lock, "sha256": "b" * 64}):
                with self.assertRaisesRegex(ValueError, "checksum"):
                    stage(
                        root / "invalid", "canary", "1.0.123", asar, archive, client=locked_client
                    )

    def test_rejects_bundled_asars_modules_and_tampered_loader(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary)
            (app / "resources").mkdir()
            loader = app / "resources/app.asar"
            loader.write_bytes(b"project-loader")
            (app / "client-distribution.json").write_text(
                json.dumps(
                    {
                        "mode": "module-only",
                        "bundled_discord_client": False,
                        "loader_sha256": sha256(loader),
                    }
                )
            )
            validate_distribution(app)
            for name in (
                "resources/discord.asar",
                "discord_voice.node",
                "updater_bootstrap",
                "client.distro",
                "discord_loading.webm",
                "whitney_400.woff",
                "ggsans-400-normal.woff2",
                "ggsans-500-medium.woff2",
                "connecting-dark.webm",
                "connecting-light.webm",
            ):
                path = app / name
                path.write_bytes(b"unwanted client")
                with self.assertRaisesRegex(ValueError, "cannot be shipped"):
                    validate_distribution(app)
                path.unlink()
            loader.write_bytes(b"Discord client")
            with self.assertRaisesRegex(ValueError, "project-owned"):
                validate_distribution(app)


if __name__ == "__main__":
    unittest.main()
