"""Portable updater dependencies are pinned, local and include their notices."""

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from common import sha256
from updater_python import sources, stage


class UpdaterPython(unittest.TestCase):
    def test_sources_use_locked_linux_and_platform_independent_wheels(self):
        locked = sources()
        self.assertEqual(set(locked), {"capstone", "pyelftools"})
        self.assertIn("manylinux_2_17_x86_64", locked["capstone"]["url"])
        self.assertTrue(locked["pyelftools"]["url"].endswith("py3-none-any.whl"))

    def test_staging_checks_hashes_and_includes_library_license_without_installer_scripts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "fixture.whl"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("capstone/__init__.py", "VERSION = 'fixture'\n")
                bundle.writestr("capstone/lib/libcapstone.so", b"library")
                bundle.writestr("capstone-1.dist-info/LICENSE.TXT", "license")
                bundle.writestr("capstone-1.data/scripts/unwanted", "installer")
            source = {"url": "https://example.invalid/fixture.whl", "sha256": sha256(archive)}
            with patch("updater_python.sources", return_value={"capstone": source}):
                stage(root / "app", root)
            self.assertEqual(
                (root / "app/python/capstone/lib/libcapstone.so").read_bytes(), b"library"
            )
            self.assertEqual(
                (root / "app/python/capstone-1.dist-info/LICENSE.TXT").read_text(), "license"
            )
            self.assertFalse((root / "app/python/capstone-1.data").exists())
            source["sha256"] = "0" * 64
            with (
                patch("updater_python.sources", return_value={"capstone": source}),
                self.assertRaisesRegex(ValueError, "checksum"),
            ):
                stage(root / "invalid", root)
