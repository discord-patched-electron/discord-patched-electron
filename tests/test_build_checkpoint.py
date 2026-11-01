"""Actual compressed workspace round trips, with nanosecond Ninja dependencies."""

import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_checkpoint import pack, restore
from common import read_json, write_json


@unittest.skipUnless(shutil.which("zstd"), "zstd required for native checkpoint fixture")
class Checkpoint(unittest.TestCase):
    def fixture(self, root):
        cache = root / "cache"
        work = cache / "prerelease/identity"
        work.mkdir(parents=True)
        write_json(work / "source.json", {"source_assembled": True})
        write_json(work / "dependencies-installed.json", {"identity": "identity"})
        write_json(work / "prepared.json", {"identity": "identity"})
        nested = work / "templates"
        nested.mkdir()
        (nested / "policy.json").write_text("{}")
        os.utime(nested, ns=(1700000000123456789, 1700000000123456789))
        source = work / "header.h"
        source.write_text("fixture")
        os.utime(source, ns=(1700000000123456789, 1700000000123456789))
        (work / "link.h").symlink_to("header.h")
        return cache, work, root / "snapshot"

    def test_resume_retains_source_and_objects_without_rewriting_dependency_times(self):
        with tempfile.TemporaryDirectory() as temp:
            cache, work, snapshot = self.fixture(Path(temp))
            before = (work / "header.h").stat().st_mtime_ns
            before_directory = work.stat().st_mtime_ns
            (work / "absolute.h").symlink_to(work / "header.h")
            before_directory = work.stat().st_mtime_ns
            report = pack(cache, snapshot, "prerelease", "identity", "key")
            self.assertGreater(report["bytes"], 0)
            shutil.rmtree(cache)
            self.assertTrue(restore(cache, snapshot, "prerelease", "identity", "key"))
            self.assertEqual((work / "header.h").stat().st_mtime_ns, before)
            self.assertEqual((work / "templates").stat().st_mtime_ns, 1700000000123456789)
            self.assertEqual((work / "link.h").read_text(), "fixture")
            self.assertEqual((work / "absolute.h").read_text(), "fixture")
            # Removing the VM-specific installer marker changes only the work
            # root; actual source directories must retain their timestamps.
            self.assertGreaterEqual(work.stat().st_mtime_ns, before_directory)
            self.assertTrue((work / "prepared.json").exists())
            self.assertFalse((work / "dependencies-installed.json").exists())
            with self.assertRaisesRegex(ValueError, "empty workspace"):
                restore(cache, snapshot, "prerelease", "identity", "key")

    def test_bad_key_path_and_tampered_archive_never_extract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache, _, snapshot = self.fixture(root)
            pack(cache, snapshot, "prerelease", "identity", "key")
            shutil.rmtree(cache)
            for destination, key in ((cache, "wrong"), (root / "elsewhere", "key")):
                with self.assertRaisesRegex(ValueError, "identity or physical"):
                    restore(destination, snapshot, "prerelease", "identity", key)
                self.assertFalse(destination.exists())
            with (snapshot / "workspace.tar.zst").open("ab") as stream:
                stream.write(b"corrupt")
            with self.assertRaisesRegex(ValueError, "size or checksum"):
                restore(cache, snapshot, "prerelease", "identity", "key")
            self.assertFalse(cache.exists())

    def test_size_budget_keeps_the_previous_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            cache, _, snapshot = self.fixture(Path(temp))
            first = pack(cache, snapshot, "prerelease", "identity", "key")
            with self.assertRaisesRegex(ValueError, "size budget"):
                pack(cache, snapshot, "prerelease", "identity", "key", limit=1)
            self.assertEqual(read_json(snapshot / "checkpoint.json"), first)
            self.assertFalse((snapshot / "workspace.tar.zst.partial").exists())

    def test_escaping_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache, work, snapshot = self.fixture(root)
            (work / "escape").symlink_to("../../../outside")
            pack(cache, snapshot, "prerelease", "identity", "key")
            shutil.rmtree(cache)
            with self.assertRaises(tarfile.LinkOutsideDestinationError):
                restore(cache, snapshot, "prerelease", "identity", "key")
            self.assertFalse((root / "outside").exists())
