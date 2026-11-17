"""Fixture payloads have one owner and never modify retained runtime inputs."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fixture_storage import client_archive, copy_runtime, transient_paths


class FixtureStorage(unittest.TestCase):
    def test_repeated_runs_discard_copies_on_success_and_failure_and_keep_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "electron").write_bytes(b"verified executable")
            resources = root / "original-resources"
            resources.mkdir()
            (resources / "app.asar").write_bytes(b"verified application")
            (source / "resources").symlink_to(resources, target_is_directory=True)
            for attempt in range(3):
                output = root / str(attempt)
                output.mkdir()
                try:
                    with transient_paths(output, "runtime", "profile"):
                        copy_runtime(source, output / "runtime")
                        (output / "runtime/resources/app.asar").write_bytes(b"fixture")
                        (output / "runtime/electron").write_bytes(b"private mutation")
                        (output / "profile").mkdir()
                        (output / "report.json").write_text("retained")
                        if attempt == 1:
                            raise RuntimeError("failed fixture")
                except RuntimeError:
                    pass
                self.assertFalse((output / "runtime").exists())
                self.assertFalse((output / "profile").exists())
                self.assertEqual((output / "report.json").read_text(), "retained")
                self.assertEqual(
                    json.loads((output / "fixture-storage.json").read_text()),
                    {"cleaned": True, "transient_paths": ["runtime", "profile"]},
                )
            self.assertEqual((resources / "app.asar").read_bytes(), b"verified application")
            self.assertEqual((source / "electron").read_bytes(), b"verified executable")

    def test_concurrent_and_preexisting_payloads_are_never_cleaned_by_another_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with transient_paths(output, "runtime"):
                (output / "runtime").mkdir()
                with self.assertRaises(BlockingIOError), transient_paths(output, "runtime"):
                    self.fail("Second owner acquired an active fixture")
                self.assertTrue((output / "runtime").exists())
            (output / "runtime").mkdir()
            with self.assertRaises(ValueError), transient_paths(output, "runtime"):
                self.fail("Accepted an existing payload")
            self.assertTrue((output / "runtime").is_dir())

    def test_cleanup_never_follows_a_nested_symlink_outside_the_fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            private = root / "private"
            private.mkdir()
            (private / "profile").mkdir()
            with (
                self.assertRaisesRegex(ValueError, "escaped"),
                transient_paths(output, "result/profile"),
            ):
                (output / "result").symlink_to(private, target_is_directory=True)
            self.assertTrue((private / "profile").is_dir())

    def test_archive_cache_reuses_verified_bytes_across_output_and_url_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "downloads"
            payload = b"one exact upstream archive"
            checksum = hashlib.sha256(payload).hexdigest()
            downloads = []

            def curl(*args):
                downloads.append(args[-1])
                Path(args[args.index("--output") + 1]).write_bytes(payload)

            with patch("common.run", side_effect=curl):
                with client_archive(
                    {"url": "https://example.test/revision-1", "sha256": checksum}, cache
                ) as first:
                    self.assertTrue(first.is_file())
                with client_archive(
                    {"url": "https://example.test/revision-2", "sha256": checksum}, cache
                ) as second:
                    self.assertTrue(second.is_file())
                self.assertEqual(first, second)
                self.assertEqual(len(downloads), 1)
                first.write_bytes(b"corrupted cache")
                with client_archive(
                    {"url": "https://example.test/revision-2", "sha256": checksum}, cache
                ) as repaired:
                    self.assertTrue(repaired.is_file())
                self.assertEqual(repaired.read_bytes(), payload)
                self.assertEqual(len(downloads), 2)
