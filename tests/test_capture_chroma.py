"""Check bounded, atomic shader substitution and re-preparation identities."""

import hashlib
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import patch_capture_chroma as chroma
import patch_capture_dimensions as dimensions
import patch_decoder_eagain as decoder
import patch_vaapi_hrd as hrd
from common import ROOT


class CaptureChroma(unittest.TestCase):
    def test_exact_shader_substitution_is_atomic_idempotent_and_bounded(self):
        shader = (ROOT / "integration/video/shaders/chroma-420.spv").read_bytes()
        self.assertEqual(len(shader), chroma.SHADER_SIZE)
        self.assertEqual(hashlib.sha256(shader).hexdigest(), chroma.AFTER_SHADER)
        self.assertEqual(struct.unpack_from("<I", shader)[0], 0x07230203)
        for pinned in (False, True):
            with self.subTest(pinned=pinned), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                original_shader = b"x" * len(shader)
                original = b"prefix" + original_shader + b"suffix"
                expected = b"prefix" + shader + b"suffix"
                prefix = "PINNED_" if pinned else ""
                module = root / "voice.node"
                module.write_bytes(original)
                module.chmod(0o751)
                shared = root / "shared.node"
                os.link(module, shared)
                with patch.multiple(
                    chroma,
                    **{
                        prefix + "INPUT_SHA256": hashlib.sha256(original).hexdigest(),
                        prefix + "OUTPUT_SHA256": hashlib.sha256(expected).hexdigest(),
                        prefix + "OFFSET": 6,
                        "BEFORE_SHADER": hashlib.sha256(original_shader).hexdigest(),
                    },
                ):
                    report = chroma.patch(module)
                    self.assertFalse(report["already_patched"])
                    self.assertEqual(module.read_bytes(), expected)
                    self.assertEqual(shared.read_bytes(), original)
                    self.assertEqual(module.stat().st_mode & 0o777, 0o751)
                    inode = module.stat().st_ino
                    self.assertTrue(chroma.patch(module)["already_patched"])
                    self.assertEqual(module.stat().st_ino, inode)
                    module.write_bytes(original)
                    with patch.object(chroma, "AFTER_SHADER", "0" * 64):
                        with self.assertRaisesRegex(ValueError, "reviewed asset"):
                            chroma.patch(module)
                    self.assertEqual(module.read_bytes(), original)

    def test_unknown_or_out_of_order_native_bytes_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            module = Path(temporary) / "voice.node"
            for data in (b"unknown", b"\3\2\x23\7", b"partial shader"):
                module.write_bytes(data)
                with self.assertRaisesRegex(ValueError, "Unsupported GPU chroma"):
                    chroma.patch(module)
                self.assertEqual(module.read_bytes(), data)

    def test_final_hashes_preserve_prior_patches_and_native_abi_scopes(self):
        for final, base in chroma.SCOPE_BASES.items():
            for module in (hrd, dimensions, decoder):
                self.assertEqual(module.reviewed(final)[:2], (final, final))
            scoped = {base: {"reviewed layout": 1}}
            decoder.extend_reviewed(scoped)
            self.assertIs(scoped[final], scoped[base])
            reviewed = {base}
            decoder.extend_reviewed(reviewed)
            self.assertIn(final, reviewed)


if __name__ == "__main__":
    unittest.main()
