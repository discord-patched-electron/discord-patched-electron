"""Validate bounded GPU capture sizing and the reviewed call dispatch without a GPU."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from capture_adaptation_fixture import check_fixture
from capture_scale_scope import scope


class CaptureScaleTests(unittest.TestCase):
    def test_pixel_restrictions_and_register_dispatch(self):
        self.assertIn("real native call dispatch passed", check_fixture("scale"))

    def test_unknown_native_version_keeps_native_scaling(self):
        with tempfile.TemporaryDirectory() as temporary:
            module = Path(temporary) / "unknown.node"
            module.write_bytes(b"unreviewed")
            self.assertEqual(scope(module)["reason"], "unreviewed-native-module")
