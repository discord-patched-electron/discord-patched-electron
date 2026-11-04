"""Exercise the reviewed explicit-sync deadline correction without a GPU."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_camera_support import node_headers
from capture_sync_scope import scope
from common import ROOT


class CaptureSyncTests(unittest.TestCase):
    def test_deadline_and_native_call_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "sync-check"
            subprocess.run(
                [
                    "cc",
                    "-std=gnu11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer",
                    "-g",
                    "-I" + str(node_headers()),
                    str(ROOT / "tests/screenshare/capture-sync-check.c"),
                    "-ldl",
                    "-o",
                    str(binary),
                ],
                check=True,
            )
            subprocess.run([str(binary)], check=True)

    def test_unknown_native_version_keeps_original_wait(self):
        with tempfile.TemporaryDirectory() as temporary:
            module = Path(temporary) / "unknown.node"
            module.write_bytes(b"unreviewed")
            self.assertEqual(scope(module)["reason"], "unreviewed-native-module")
