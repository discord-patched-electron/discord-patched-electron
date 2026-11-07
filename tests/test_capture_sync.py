"""Exercise the reviewed explicit-sync deadline correction without a GPU."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from capture_adaptation_fixture import check_fixture
from capture_sync_scope import scope


class CaptureSyncTests(unittest.TestCase):
    def test_deadline_and_native_call_contract(self):
        self.assertIn("real native call dispatch passed", check_fixture("sync"))

    def test_unknown_native_version_keeps_original_wait(self):
        with tempfile.TemporaryDirectory() as temporary:
            module = Path(temporary) / "unknown.node"
            module.write_bytes(b"unreviewed")
            self.assertEqual(scope(module)["reason"], "unreviewed-native-module")
