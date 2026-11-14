import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_reference import build_config
from common import ROOT, digest, read_json, sha256
from fetch_reference import extract, fetch, validate_capacity, validate_provenance
from fingerprints import runtime_key


class ReferenceArtifact(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive = self.root / "dist.zip"
        self.output = self.root / "runtime"

    def test_verified_archive_preserves_execution_permission(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            entry = zipfile.ZipInfo("electron")
            entry.external_attr = (stat.S_IFREG | 0o755) << 16
            archive.writestr(entry, "synthetic executable")
        with self.assertRaisesRegex(ValueError, "checksum"):
            extract(self.archive, self.output, "0" * 64)
        self.assertFalse(self.output.exists())
        extract(self.archive, self.output, sha256(self.archive))
        self.assertEqual((self.output / "electron").stat().st_mode & 0o777, 0o755)

    def test_archive_escape_is_rejected_before_extraction(self):
        for name, mode in [
            ("../escape", stat.S_IFREG),
            ("/absolute", stat.S_IFREG),
            ("symlink", stat.S_IFLNK),
        ]:
            with self.subTest(name=name):
                with zipfile.ZipFile(self.archive, "w") as archive:
                    entry = zipfile.ZipInfo(name)
                    entry.external_attr = (mode | 0o755) << 16
                    archive.writestr(entry, "../../outside")
                with self.assertRaisesRegex(ValueError, "archive path"):
                    extract(self.archive, self.output, sha256(self.archive))
                self.assertFalse(self.output.exists())

    def test_candidate_provenance_requires_current_cpu_source_and_key(self):
        sources = read_json(ROOT / "config/sources.lock.json")
        spec = sources["targets"]["prerelease"]
        config = build_config("prerelease")
        key = runtime_key(spec, sources, config)
        capacity = {
            "source": {
                "target": "prerelease",
                "upstream_commit": spec["commit"],
                "source_assembled": True,
                "dependencies_patched": True,
                "compiled": True,
            },
            "config": config,
            "runtime_key": key,
            "build_key": key,
            "compiled_version": spec["version"],
        }
        validate_capacity(capacity, candidate=True)
        for changed in [
            {"runtime_key": "b" * 64},
            {"compiled_version": "44.0.0"},
            {"config": {**config, "cpu_baseline": "x86-64"}},
            {"source": {**capacity["source"], "compiled": False}},
        ]:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                validate_capacity({**capacity, **changed}, candidate=True)

    def test_reviewed_driver_migration_is_exact_and_never_skips_source_checks(self):
        sources = read_json(ROOT / "config/sources.lock.json")
        spec = sources["targets"]["prerelease"]
        config = build_config("prerelease")
        expected = runtime_key(spec, sources, config)
        capacity = {
            "source": {
                "target": "prerelease",
                "upstream_commit": spec["commit"],
                "source_assembled": True,
                "dependencies_patched": True,
                "compiled": True,
            },
            "config": config,
            "compiled_version": spec["version"],
            "runtime_key": "old",
            "build_key": "old",
            "archive_sha256": "archive",
        }
        policy = {
            "schema": 1,
            "reviewed_reports": [
                {
                    "expected_runtime_key": expected,
                    "run": "123",
                    "compiler_commit": "commit",
                    "capacity_sha256": digest(capacity),
                    "runtime_key": "old",
                }
            ],
        }

        def read(path):
            return policy if path.name == "runtime-compatibility.json" else read_json(path)

        with patch("fetch_reference.read_json", side_effect=read):
            validate_capacity(capacity, True, run_id="123", compiler_commit="commit")
            provenance = {
                "capacity": capacity,
                "compiler_commit": "commit",
                "compiler_run": "https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/123",
            }
            validate_provenance(provenance)
            validate_provenance(provenance, True)
            for change in (
                {"compiler_commit": "other"},
                {"compiler_run": "https://github.com/other/repo/actions/runs/123"},
                {"compiler_run": "123"},
            ):
                with self.assertRaises(ValueError):
                    validate_provenance({**provenance, **change})

            for run_id, commit in (("other", "commit"), ("123", "other"), (None, None)):
                with self.assertRaises(ValueError):
                    validate_capacity(capacity, True, run_id=run_id, compiler_commit=commit)
            for change in (
                {"archive_sha256": "changed"},
                {"config": {}},
                {"source": {**capacity["source"], "compiled": False}},
            ):
                with self.assertRaises(ValueError):
                    validate_capacity(
                        {**capacity, **change}, True, run_id="123", compiler_commit="commit"
                    )
            with (
                patch("fetch_reference.runtime_key", return_value="new-inputs"),
                self.assertRaises(ValueError),
            ):
                validate_capacity(capacity, True, run_id="123", compiler_commit="commit")

    @patch.dict(os.environ, {"GH_REPO": "discord-patched-electron/discord-patched-electron"})
    def test_development_download_requires_explicit_opt_in_and_manual_project_build(self):
        valid = {
            "conclusion": "success",
            "head_branch": "dev",
            "event": "workflow_dispatch",
            "path": ".github/workflows/build.yml",
            "run_attempt": 1,
            "head_repository": {"full_name": "discord-patched-electron/discord-patched-electron"},
        }
        for change in (
            {},
            {"head_branch": "other"},
            {"event": "pull_request"},
            {"path": ".github/workflows/import-runtime.yml"},
            {"conclusion": "failure"},
            {"head_repository": {"full_name": "someone/fork"}},
        ):
            with (
                self.subTest(change=change),
                patch(
                    "fetch_reference.subprocess.check_output",
                    return_value=json.dumps(valid | change),
                ),
                patch(
                    "fetch_reference.run", side_effect=RuntimeError("download reached")
                ) as download,
            ):
                with self.assertRaisesRegex(ValueError, "successful main-branch"):
                    fetch("123", self.root / "default")
                download.assert_not_called()
                if change:
                    with self.assertRaisesRegex(ValueError, "successful main-branch"):
                        fetch("123", self.root / "opt-in", allow_development=True)
                    download.assert_not_called()
                else:
                    with self.assertRaisesRegex(RuntimeError, "download reached"):
                        fetch("123", self.root / "opt-in", allow_development=True)
                    download.assert_called_once()

    @patch.dict(os.environ, {"GH_REPO": "discord-patched-electron/discord-patched-electron"})
    def test_successful_main_import_run_keeps_local_compiler_provenance(self):
        sources = read_json(ROOT / "config/sources.lock.json")
        spec = sources["targets"]["prerelease"]
        config = build_config("prerelease")
        key = runtime_key(spec, sources, config)
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("electron", "owned runtime fixture")
        capacity = {
            "source": {
                "target": "prerelease",
                "upstream_commit": spec["commit"],
                "source_assembled": True,
                "dependencies_patched": True,
                "compiled": True,
            },
            "config": config,
            "runtime_key": key,
            "build_key": key,
            "compiled_version": spec["version"],
            "archive_sha256": sha256(self.archive),
        }
        local = {"compiler_provider": "local", "native_inputs_commit": "a" * 40}
        metadata = {
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": "b" * 40,
            "path": ".github/workflows/import-runtime.yml",
            "run_attempt": 1,
            "html_url": "https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/123",
            "head_repository": {"full_name": "discord-patched-electron/discord-patched-electron"},
        }

        def download(*args):
            directory = Path(args[args.index("--dir") + 1])
            directory.mkdir(parents=True)
            if args[args.index("--name") + 1] == "candidate-capacity-123-1":
                (directory / "capacity.json").write_text(json.dumps(capacity))
                (directory / "local-build-provenance.json").write_text(json.dumps(local))
            else:
                self.assertIn("candidate-runtime-123", args)
                (directory / "runtime-dist.zip").write_bytes(self.archive.read_bytes())

        with (
            patch("fetch_reference.subprocess.check_output", return_value=json.dumps(metadata)),
            patch("fetch_reference.run", side_effect=download),
        ):
            fetch("123", self.output)
        provenance = read_json(self.output / "artifact-provenance.json")
        self.assertEqual(provenance["compiler_provider"], "local")
        self.assertEqual(provenance["local_build_provenance"], local)
        self.assertFalse(provenance["publishable"])

    @patch.dict(os.environ, {"GH_REPO": "discord-patched-electron/discord-patched-electron"})
    def test_cached_import_retains_provenance_and_rejects_changed_upload_bytes(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("electron", "owned runtime fixture")
        capacity = {"archive_sha256": sha256(self.archive)}
        local = {"compiler_provider": "local", "native_inputs_commit": "a" * 40}
        payload = json.dumps(local).encode()
        metadata = {
            "conclusion": "success",
            "head_branch": "main",
            "head_sha": "b" * 40,
            "path": ".github/workflows/import-runtime.yml",
            "run_attempt": 1,
            "html_url": "https://github.com/discord-patched-electron/discord-patched-electron/actions/runs/123",
            "head_repository": {"full_name": "discord-patched-electron/discord-patched-electron"},
        }
        good = {
            "run": "123",
            "compiler_commit": metadata["head_sha"],
            "cache_release": "runtime-cache-fixture",
            "capacity_sha256": digest(capacity),
        }

        def read(path):
            if path.name == "update-policy.json":
                return {"last_good_runtime": good}
            return read_json(path)

        def download(*args):
            self.assertIn("release", args)
            name = args[args.index("--pattern") + 1]
            directory = Path(args[args.index("--dir") + 1])
            directory.mkdir(parents=True, exist_ok=True)
            data = {
                "capacity.json": json.dumps(capacity).encode(),
                "local-build-provenance.json": payload,
                "runtime-dist.zip": self.archive.read_bytes(),
            }
            (directory / name).write_bytes(data[name])

        for changed in (False, True):
            with self.subTest(changed=changed):
                output = self.output / str(changed)
                cache = {
                    "assets": [
                        {
                            "name": "local-build-provenance.json",
                            "digest": "sha256:"
                            + ("0" * 64 if changed else hashlib.sha256(payload).hexdigest()),
                        }
                    ],
                }
                with (
                    patch("fetch_reference.read_json", side_effect=read),
                    patch("fetch_reference.validate_capacity"),
                    patch("fetch_reference.run", side_effect=download),
                    patch(
                        "fetch_reference.subprocess.check_output",
                        side_effect=[json.dumps(metadata), json.dumps(cache)],
                    ),
                ):
                    if changed:
                        with self.assertRaisesRegex(ValueError, "provenance checksum"):
                            fetch("123", output)
                        self.assertFalse((output / "runtime").exists())
                    else:
                        fetch("123", output)
                        provenance = read_json(output / "artifact-provenance.json")
                        self.assertEqual(provenance["local_build_provenance"], local)
                        self.assertEqual(provenance["compiler_commit"], metadata["head_sha"])

    @patch.dict(os.environ, {"GH_REPO": "discord-patched-electron/discord-patched-electron"})
    @patch("fetch_reference.run")
    @patch("fetch_reference.subprocess.check_output")
    def test_failed_or_untrusted_compiler_run_is_not_downloaded(self, query, download):
        valid = {
            "conclusion": "success",
            "head_branch": "main",
            "path": ".github/workflows/capacity.yml",
            "head_repository": {"full_name": "discord-patched-electron/discord-patched-electron"},
        }
        for change in [
            {"conclusion": "failure"},
            {"head_branch": "unreviewed"},
            {"path": ".github/workflows/check.yml"},
            {"head_repository": {"full_name": "someone/fork"}},
        ]:
            query.return_value = json.dumps({**valid, **change})
            with self.assertRaisesRegex(ValueError, "successful main-branch"):
                fetch("123", self.output)
            download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
