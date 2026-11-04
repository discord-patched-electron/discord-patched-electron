import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from capture_picker_scope import scope
from common import ROOT


class CapturePicker(unittest.TestCase):
    def test_unknown_module_is_not_treated_as_reviewed(self):
        with tempfile.TemporaryDirectory() as temporary:
            module = Path(temporary) / "discord_voice.node"
            module.write_bytes(b"unreviewed native module")
            self.assertEqual(
                scope(module),
                {"schema": 1, "supported": False, "reason": "unreviewed-native-module"},
            )

    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_native_picker_ownership_and_thread_lifecycle(self):
        self.run_fixture("capture-picker-check.c", "FD transfer")

    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_native_delivery_identity_and_fast_cadence(self):
        self.run_fixture("capture-delivery-check.c", "delivery identity leases")

    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_native_publication_wake_races_and_shutdown(self):
        self.run_fixture("capture-wake-check.c", "publication wake races")

    def run_fixture(self, source, expected):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "check"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-O1",
                    "-g",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer",
                    "-I/usr/include/node",
                    str(ROOT / "tests/screenshare" / source),
                    "-pthread",
                    "-ldl",
                    "-o",
                    str(binary),
                ],
                check=True,
            )
            result = subprocess.run([binary], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(expected, result.stdout)


if __name__ == "__main__":
    unittest.main()
