"""Verify codec acquisition, repair and cache selection without network access."""

import bz2
import concurrent.futures
import hashlib
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration/updater"))
import openh264


class OpenH264(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="codec profile ")
        self.addCleanup(self.temporary.cleanup)
        self.profile = Path(self.temporary.name)
        self.library = b"\x7fELF" + bytes(range(256)) * 4
        self.archive = bz2.compress(self.library)
        self.manifest = {
            "filename": "libopenh264-2.5.1-linux64.7.so",
            "url": "https://ciscobinary.openh264.org/fixture.bz2",
            "library_size": len(self.library),
            "library_sha256": hashlib.sha256(self.library).hexdigest(),
            "archive_size": len(self.archive),
            "archive_sha256": hashlib.sha256(self.archive).hexdigest(),
        }
        self.metadata = self.profile / "manifest.json"
        self.metadata.write_text(json.dumps(self.manifest))
        self.path = self.profile / "dpe-codecs/openh264" / self.manifest["filename"]
        manifest_patch = patch.object(openh264, "MANIFEST", self.metadata)
        manifest_patch.start()
        self.addCleanup(manifest_patch.stop)

    def download(self, _url, path, **_kwargs):
        path.write_bytes(self.archive)

    def test_fresh_install_and_offline_reuse_without_modifying_valid_cache(self):
        with patch.object(openh264, "download", side_effect=self.download) as download:
            self.assertEqual(openh264.ensure(self.profile), self.path)
            before = self.path.stat()
            self.assertEqual(openh264.ensure(self.profile, offline=True), self.path)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(self.path.read_bytes(), self.library)
        self.assertEqual(self.path.stat().st_ino, before.st_ino)
        self.assertEqual(self.path.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o644)

    def test_concurrent_starts_download_once(self):
        with (
            patch.object(openh264, "download", side_effect=self.download) as download,
            concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool,
        ):
            results = list(pool.map(lambda _: openh264.ensure(self.profile), range(4)))
        self.assertEqual(results, [self.path] * 4)
        self.assertEqual(download.call_count, 1)

    def test_discord_asset_cache_cleanup_cannot_remove_managed_codec(self):
        discord_cache = self.profile / "discord_asset_cache/openh264"
        discord_cache.mkdir(parents=True)
        upstream = discord_cache / "libopenh264-2.7.0-linux64.8.so"
        upstream.write_bytes(b"Discord owns this independent cache")
        with patch.object(openh264, "download", side_effect=self.download):
            openh264.ensure(self.profile)
        self.assertEqual(upstream.read_bytes(), b"Discord owns this independent cache")
        upstream.unlink()
        discord_cache.rmdir()
        self.assertEqual(openh264.ensure(self.profile, offline=True), self.path)

    def test_corrupt_cache_repaired_atomically(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"bad")

        def download(url, path, **kwargs):
            self.assertEqual(self.path.read_bytes(), b"bad")
            self.download(url, path, **kwargs)

        with patch.object(openh264, "download", side_effect=download):
            openh264.ensure(self.profile)
        self.assertEqual(self.path.read_bytes(), self.library)
        self.assertFalse(list(self.path.parent.glob(".dpe-openh264-*")))

    def test_failed_integrity_check_preserves_old_bytes_and_leaves_no_partial_cache(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"old invalid cache")
        bad_archive = b"a" * len(self.archive)
        with patch.object(
            openh264, "download", side_effect=lambda _, p, **kw: p.write_bytes(bad_archive)
        ) as download:
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                openh264.ensure(self.profile)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(self.path.read_bytes(), b"old invalid cache")
        self.assertFalse(list(self.path.parent.glob(".dpe-openh264-*")))

    def test_verified_archive_still_requires_exact_bounded_library(self):
        for data in (self.archive[:-4], bz2.compress(self.library * 200), self.archive + b"extra"):
            with self.subTest(size=len(data)):
                manifest = {
                    **self.manifest,
                    "archive_size": len(data),
                    "archive_sha256": hashlib.sha256(data).hexdigest(),
                }
                self.metadata.write_text(json.dumps(manifest))
                with patch.object(
                    openh264,
                    "download",
                    side_effect=lambda _, p, data=data, **kw: p.write_bytes(data),
                ):
                    with self.assertRaises(ValueError):
                        openh264.ensure(self.profile)
                self.assertFalse(self.path.exists())

    def test_transient_failure_retries_but_permanent_http_failure_does_not(self):
        failures = [urllib.error.URLError("interrupted"), None]

        def download(url, path, **kwargs):
            if error := failures.pop(0):
                path.write_bytes(b"partial")
                raise error
            self.download(url, path, **kwargs)

        with patch.object(openh264, "download", side_effect=download) as mocked:
            openh264.ensure(self.profile)
        self.assertEqual(mocked.call_count, 2)
        self.path.unlink()
        with patch.object(
            openh264,
            "download",
            side_effect=urllib.error.HTTPError("url", 404, "Not found", None, None),
        ) as mocked:
            with self.assertRaisesRegex(ValueError, "404"):
                openh264.ensure(self.profile)
        self.assertEqual(mocked.call_count, 1)

    def test_symlink_repair_does_not_overwrite_its_target(self):
        other = self.profile / "other.so"
        other.write_bytes(b"other")
        self.path.parent.mkdir(parents=True)
        self.path.symlink_to(other)
        with patch.object(openh264, "download", side_effect=self.download):
            openh264.ensure(self.profile)
        self.assertFalse(self.path.is_symlink())
        self.assertEqual(other.read_bytes(), b"other")

    def test_offline_missing_or_corrupt_cache_never_attempts_network(self):
        with patch.object(openh264, "download") as download:
            with self.assertRaisesRegex(ValueError, "not cached or needs repair"):
                openh264.ensure(self.profile, offline=True)
            self.path.parent.mkdir(parents=True)
            self.path.write_bytes(b"bad")
            with self.assertRaisesRegex(ValueError, "not cached or needs repair"):
                openh264.ensure(self.profile, offline=True)
        download.assert_not_called()

    def test_final_flag_selection_and_original_offline_mode(self):
        with patch.object(openh264, "ensure") as ensure, patch.dict(os.environ, {}, clear=True):
            for path in ("", str(self.profile / "custom.so")):
                os.environ["DPE_OPENH264_LIBRARY"] = path
                self.assertIsNone(openh264.ensure_requested(self.profile))
            ensure.assert_not_called()
            os.environ.update(
                DPE_OPENH264_LIBRARY=str(self.path),
                DPE_CLIENT_OFFLINE="1",
                DPE_OPENH264_OFFLINE="0",
            )
            openh264.ensure_requested(self.profile)
            ensure.assert_called_once_with(self.profile, offline=False)
            os.environ["DPE_OPENH264_OFFLINE"] = "1"
            openh264.ensure_requested(self.profile)
            ensure.assert_called_with(self.profile, offline=True)


if __name__ == "__main__":
    unittest.main()
