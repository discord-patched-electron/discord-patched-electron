"""Exercise module-only updates, atomic activation and archive boundaries."""

import copy
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
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
            with patch.object(
                client, "latest", return_value=client.normalize(manifest(), "canary")
            ):
                first = client.ensure_client(root, "canary")
                self.assertEqual(client.ensure_client(root, "canary"), first)
            self.assertEqual(len(downloaded), len(client.REQUIRED))
            pointer = (root / "current.json").read_bytes()
            updated = manifest(124)
            updated["modules"]["discord_voice"]["full"]["package_sha256"] = "c" * 64
            with (
                patch.object(client, "latest", return_value=client.normalize(updated, "canary")),
                patch.object(client, "fetch", side_effect=OSError("offline")),
            ):
                self.assertEqual(client.ensure_client(root, "canary"), first)
            self.assertEqual((root / "current.json").read_bytes(), pointer)
            with patch.object(client, "latest", return_value=client.normalize(updated, "canary")):
                second = client.ensure_client(root, "canary")
            self.assertNotEqual(second, first)
            self.assertEqual(len(downloaded), len(client.REQUIRED) + 1)
            self.assertEqual(client.current_host(root, "canary"), second)
            with patch.object(
                client, "latest", side_effect=AssertionError("offline must not use network")
            ):
                self.assertEqual(client.ensure_client(root, "canary", offline=True), second)

    def test_first_install_failure_does_not_publish_pointer(self):
        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(client, "latest", side_effect=OSError("offline")),
        ):
            root = Path(temporary)
            with self.assertRaises(OSError):
                client.ensure_client(root, "canary")
            self.assertFalse((root / "current.json").exists())
            with self.assertRaisesRegex(ValueError, "internet"):
                client.ensure_client(root, "canary", offline=True)

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
            with patch.object(
                client, "latest", return_value=client.normalize(manifest(), "canary")
            ):
                previous = client.ensure_client(root, "canary")
            commands = [call.args[0] for call in run.call_args_list]
            self.assertTrue(any(command[1].endswith("patches.cjs") for command in commands))
            updated = client.normalize(manifest(124), "canary")
            updated["modules"]["discord_desktop_core"]["sha256"] = "d" * 64
            run.side_effect = subprocess.CalledProcessError(1, "desktop-preflight")
            with patch.object(client, "latest", return_value=updated):
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
                return io.BytesIO(b"incorrect bytes")

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(client.urllib.request, "build_opener", return_value=Opener()),
        ):
            archive = Path(temporary) / "module.br"
            with self.assertRaisesRegex(ValueError, "checksum"):
                client.fetch("https://canary.dl2.discordapp.net/test", archive, "a" * 64)
            with self.assertRaisesRegex(ValueError, "size limit"):
                client.fetch("https://canary.dl2.discordapp.net/test", archive, "a" * 64, limit=2)

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


class DistributionBoundary(unittest.TestCase):
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
            with patch("stage_updater.read_json", return_value=lock):
                stage(app, "canary", "1.0.123", asar, archive)
            validate_distribution(app)
            unpacked = root / "unpacked"
            subprocess.run(
                [asar, "extract", str(app / "resources/app.asar"), str(unpacked)], check=True
            )
            self.assertEqual({p.name for p in unpacked.iterdir()}, {"package.json", "main.cjs"})
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
            ):
                self.assertEqual((app / name).stat().st_mode & 0o444, 0o444)
            with patch("stage_updater.read_json", return_value={**lock, "sha256": "b" * 64}):
                with self.assertRaisesRegex(ValueError, "checksum"):
                    stage(root / "invalid", "canary", "1.0.123", asar, archive)

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
