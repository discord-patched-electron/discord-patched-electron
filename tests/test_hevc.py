"""Check private codec builds and adapter ownership without starting a GPU."""

import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_hevc_support as hevc
from common import ROOT, digest, hash_files, sha256, write_json
from extract_screenshare_hevc import frame_nalus


class HevcSupport(unittest.TestCase):
    def test_complete_corresponding_source_is_staged_and_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "upstream.tar.xz"
            source.write_bytes(b"upstream source fixture")
            with patch.object(hevc, "ARCHIVE_SHA256", sha256(source)):
                staged = hevc.stage_source(root / "application", source)
                self.assertEqual(staged.read_bytes(), source.read_bytes())
                self.assertEqual(staged.name, hevc.SOURCE_ARCHIVE)
                source.write_bytes(b"corrupted archive")
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    hevc.stage_source(root / "another", source)

    def test_hevc_rtp_single_aggregation_and_fragment_reconstruction(self):
        vps, sps = b"\x40\x01vps", b"\x42\x01sps"
        aggregation = (
            b"\x60\x01" + len(vps).to_bytes(2, "big") + vps + len(sps).to_bytes(2, "big") + sps
        )
        fragments = [b"\x62\x01\x93head", b"\x62\x01\x13body", b"\x62\x01\x53tail"]
        expected = b"\0\0\0\1" + vps + b"\0\0\0\1" + sps + b"\0\0\0\1\x26\x01headbodytail"
        self.assertEqual(frame_nalus([aggregation, *fragments]), expected)
        self.assertEqual(frame_nalus([vps]), b"\0\0\0\1" + vps)
        for payloads in (
            [],
            [b""],
            [b"\xc0\x01x"],
            [b"\x40\x00x"],
            [aggregation[:-1]],
            [b"\x60\x01\0\0"],
            [fragments[0]],
            fragments[1:],
            [fragments[0], vps],
            [fragments[0], b"\x62\x01\x54x"],
            [b"\x62\x01\xd3x"],
            [b"\x64\x01x"],
        ):
            with self.subTest(payloads=payloads), self.assertRaises(ValueError):
                frame_nalus(payloads)

    def test_native_frame_abi_leases_and_destroy_during_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "hevc-check"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-pthread",
                    str(ROOT / "tests/screenshare/hevc-check.c"),
                    "-ldl",
                    "-o",
                    str(binary),
                ],
                check=True,
            )
            subprocess.run([str(binary)], check=True, timeout=15)

    def source_fixture(self, root, text):
        archive = root / f"ffmpeg-{hevc.VERSION}.tar.xz"
        data = text.encode()
        with tarfile.open(archive, "w:xz") as stream:
            item = tarfile.TarInfo(f"ffmpeg-{hevc.VERSION}/libavcodec/vaapi_encode.c")
            item.size = len(data)
            stream.addfile(item, io.BytesIO(data))
            data = b"    VAEncMiscParameterRateControl rc_params;\n"
            item = tarfile.TarInfo(f"ffmpeg-{hevc.VERSION}/libavcodec/vaapi_encode.h")
            item.size = len(data)
            stream.addfile(item, io.BytesIO(data))
            data = b"#if VA_CHECK_VERSION(1, 13, 0)\n    // update sps setting according to queried result\n"
            item = tarfile.TarInfo(f"ffmpeg-{hevc.VERSION}/libavcodec/vaapi_encode_h265.c")
            item.size = len(data)
            stream.addfile(item, io.BytesIO(data))
        return archive

    def test_pinned_source_patch_is_idempotent_and_rejects_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = self.source_fixture(
                root,
                """    if (base_pic->type == FF_HW_PICTURE_TYPE_IDR) {
        for (i = 0; i < ctx->nb_global_params; i++) {""",
            )
            with patch.object(hevc, "ARCHIVE_SHA256", sha256(archive)):
                source = hevc.prepare_source(root)
                file = source / "libavcodec/vaapi_encode.c"
                self.assertIn("DPE: submit current RC/HRD/FPS", file.read_text())
                timestamp = file.stat().st_mtime_ns
                self.assertEqual(hevc.prepare_source(root), source)
                self.assertEqual(file.stat().st_mtime_ns, timestamp)
                file.write_text("upstream no longer matches")
                with self.assertRaisesRegex(ValueError, "no longer matches"):
                    hevc.prepare_source(root)
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                hevc.prepare_source(root)

    def test_verified_cache_reuse_starts_no_build_or_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / "cache"
            cache.mkdir()
            asset = cache / hevc.ASSET
            asset.write_bytes(b"cached private codec fixture")
            identity = digest(
                {
                    "archive": hevc.ARCHIVE_SHA256,
                    "configure": hevc.CONFIGURE,
                    "compiler": "fixture compiler",
                    "source": hash_files(
                        [
                            hevc.SOURCE,
                            hevc.HEADER,
                            "integration/video/av1-level.h",
                            "integration/video/hevc-level.h",
                            "scripts/build_hevc_support.py",
                        ]
                    ),
                }
            )
            manifest = {"source_key": identity, "sha256": sha256(asset)}
            write_json(cache / "hevc-support.json", manifest)
            with (
                patch.object(hevc.subprocess, "check_output", return_value="fixture compiler"),
                patch.object(
                    hevc.subprocess, "run", side_effect=AssertionError("compiler restarted")
                ),
                patch.object(hevc, "prepare_source", side_effect=AssertionError("source fetched")),
            ):
                self.assertEqual(hevc.build(root / "result", cache=cache), manifest)
                self.assertEqual((root / "result" / hevc.ASSET).read_bytes(), asset.read_bytes())
                asset.write_bytes(b"corrupted")
                with self.assertRaisesRegex(AssertionError, "source fetched"):
                    hevc.build(root / "second", cache=cache)


if __name__ == "__main__":
    unittest.main()
