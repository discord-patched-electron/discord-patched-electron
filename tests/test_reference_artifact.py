import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import sha256
from fetch_reference import extract, fetch


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
        for name, mode in [("../escape", stat.S_IFREG), ("/absolute", stat.S_IFREG),
                           ("symlink", stat.S_IFLNK)]:
            with self.subTest(name=name):
                with zipfile.ZipFile(self.archive, "w") as archive:
                    entry = zipfile.ZipInfo(name)
                    entry.external_attr = (mode | 0o755) << 16
                    archive.writestr(entry, "../../outside")
                with self.assertRaisesRegex(ValueError, "archive path"):
                    extract(self.archive, self.output, sha256(self.archive))
                self.assertFalse(self.output.exists())

    @patch.dict(os.environ, {"GH_REPO": "discord-patched-electron/discord-patched-electron"})
    @patch("fetch_reference.run")
    @patch("fetch_reference.subprocess.check_output")
    def test_failed_or_untrusted_compiler_run_is_not_downloaded(self, query, download):
        valid = {"conclusion": "success", "head_branch": "main", "path": ".github/workflows/capacity.yml",
            "head_repository": {"full_name": "discord-patched-electron/discord-patched-electron"}}
        for change in [{"conclusion": "failure"}, {"head_branch": "unreviewed"},
                       {"path": ".github/workflows/check.yml"},
                       {"head_repository": {"full_name": "someone/fork"}}]:
            query.return_value = json.dumps({**valid, **change})
            with self.assertRaisesRegex(ValueError, "successful main-branch"):
                fetch("123", self.output)
            download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
