import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import write_json
from stage_capture_backends import COMMIT, REQUIRED, stage, validate


class CaptureBundle(unittest.TestCase):
    def test_complete_sources_checksums_and_prebuilt_reuse(self):
        def wayland(output):
            output.mkdir()
            (output / "dpe-wayland-source").write_bytes(b"wayland binary fixture")
            (output / "dpe-wayland-source").chmod(0o755)
            write_json(output / "wayland-source.json", {})

        def game(output, archive):
            output.mkdir()
            for relative in REQUIRED:
                if relative.startswith("vkcapture/"):
                    file = output / relative.removeprefix("vkcapture/")
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes(b"game bundle fixture")
            source = output / f"source/obs-vkcapture-{COMMIT}"
            (source / "src").mkdir(parents=True)
            (source / "LICENSE").write_text("GPL fixture")
            (source / "src/vkcapture-hook.c").write_text("corresponding source fixture")
            (output / "build").mkdir()
            (output / "build/private-object").touch()

        def kms(output):
            output.mkdir()
            (output / "dpe-kms-source").write_bytes(b"KMS binary fixture")
            (output / "dpe-kms-source").chmod(0o755)
            write_json(output / "kms-source.json", {})
            (output / "dpe-kms-server").write_bytes(b"KMS server fixture")
            (output / "dpe-kms-server").chmod(0o755)
            write_json(output / "kms-server.json", {})

        def preview(output):
            output.mkdir()
            (output / "dpe-screen-preview").write_bytes(b"GPU preview fixture")
            (output / "dpe-screen-preview").chmod(0o755)
            write_json(output / "screen-preview.json", {})

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch("stage_capture_backends.build_wayland", side_effect=wayland),
                patch("stage_capture_backends.build_game", side_effect=game),
                patch("stage_capture_backends.build_kms", side_effect=kms),
                patch("stage_capture_backends.build_preview", side_effect=preview),
            ):
                result = stage(root / "built", archive=root / "pinned.tar.gz")
            self.assertGreater(len(result["files"]), len(REQUIRED))
            self.assertFalse((root / "built/vkcapture/build").exists())
            with patch(
                "stage_capture_backends.build_wayland",
                side_effect=AssertionError("unexpected build"),
            ):
                self.assertEqual(stage(root / "copy", prebuilt=root / "built"), result)
            self.assertTrue((root / "copy/dpe-wayland-source").stat().st_mode & 0o111)
            (root / "copy/vkcapture/libobs_glcapture.so").write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                validate(root / "copy")
            shutil.rmtree(root / "copy")
            (root / "built/unlisted").symlink_to("/etc/passwd")
            with self.assertRaises(ValueError):
                stage(root / "copy", prebuilt=root / "built")
            self.assertFalse((root / "copy").exists())


if __name__ == "__main__":
    unittest.main()
