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
from common import ROOT, read_json, sha256
from fetch_reference import extract, fetch, validate_capacity
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
