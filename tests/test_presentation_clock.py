"""Presentation clock correction changes only the reviewed direct sink load."""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import patch_presentation_clock as clock


class PresentationClock(unittest.TestCase):
    def test_exact_load_width_and_clock_offset_only(self):
        before = b"prefix" + clock.BEFORE + b"suffix"
        after = b"prefix" + clock.AFTER + b"suffix"
        reviewed = ((hashlib.sha256(before).hexdigest(), hashlib.sha256(after).hexdigest(), 6),)
        with patch.object(clock, "REVIEWED", reviewed):
            self.assertEqual(clock.corrected(before), after)
            self.assertEqual(clock.corrected(after), after)
            self.assertEqual(sum(a != b for a, b in zip(before, after, strict=True)), 2)
            with tempfile.TemporaryDirectory() as root:
                f = Path(root) / "voice.node"
                f.write_bytes(before)
                f.chmod(0o755)
                result = clock.patch(f)
                self.assertEqual(result["changed_bytes"], 2)
                self.assertEqual(f.stat().st_mode & 0o777, 0o755)
                self.assertTrue(clock.patch(f)["already_patched"])
        with self.assertRaisesRegex(ValueError, "Unreviewed"):
            clock.corrected(before + b"changed")

    def test_no_broader_native_review_from_a_clock_patch(self):
        import encoder_scope

        for output, base in clock.OUTPUT_BASES.items():
            self.assertIn(output, encoder_scope.REVIEWED)
            self.assertIn(base, encoder_scope.REVIEWED)
