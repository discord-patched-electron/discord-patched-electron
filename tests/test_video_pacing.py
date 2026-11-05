"""The scene-change correction changes only a reviewed screenshare default."""

import ctypes
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import patch_video_pacing as pacing


class VideoPacing(unittest.TestCase):
    def test_reviewed_default_only_is_atomic_and_idempotent(self):
        before = b"prefix" + pacing.BEFORE + b"explicit field-trial parser unchanged"
        after = b"prefix" + pacing.AFTER + b"explicit field-trial parser unchanged"
        reviewed = ((hashlib.sha256(before).hexdigest(), hashlib.sha256(after).hexdigest(), 6),)
        with patch.object(pacing, "REVIEWED", reviewed), tempfile.TemporaryDirectory() as root:
            binary = Path(root) / "voice.node"
            binary.write_bytes(before)
            binary.chmod(0o755)
            report = pacing.patch(binary)
            self.assertEqual(binary.read_bytes(), after)
            self.assertEqual(report["changed_bytes"], 6)
            self.assertEqual(binary.stat().st_mode & 0o777, 0o755)
            self.assertTrue(pacing.patch(binary)["already_patched"])
            binary.write_bytes(before + b"unknown")
            with self.assertRaisesRegex(ValueError, "Unreviewed"):
                pacing.patch(binary)
            self.assertEqual(binary.read_bytes(), before + b"unknown")

    def test_native_alr_parser_accepts_the_bounded_policy(self):
        # WebRTC uses this scanf format. In particular, the zero-padded decimal
        # queue limit must parse as 50, without changing ALR budget/group fields.
        scanf = ctypes.CDLL(None).sscanf
        scanf.restype = ctypes.c_int
        for value, expected in ((pacing.BEFORE, (1.0, 2875)), (pacing.AFTER, (2.5, 50))):
            factor = ctypes.c_float()
            queue = ctypes.c_int64()
            fields = [ctypes.c_int() for _ in range(4)]
            count = scanf(
                value,
                b"%f,%ld,%d,%d,%d,%d",
                ctypes.byref(factor),
                ctypes.byref(queue),
                *(ctypes.byref(field) for field in fields),
            )
            self.assertEqual(count, 6)
            self.assertEqual((factor.value, queue.value), expected)
            self.assertEqual([field.value for field in fields], [80, 40, -60, 3])

    def test_paced_modules_keep_the_clock_and_compatibility_scopes(self):
        import encoder_scope
        import patch_presentation_clock as clock

        for output, before in pacing.OUTPUT_BASES.items():
            self.assertIn(output, encoder_scope.REVIEWED)
            self.assertEqual(clock.OUTPUT_BASES[output], clock.OUTPUT_BASES[before])
