import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_camera_support import build, node_headers
from build_rust_support import build as build_rust
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

    def test_reviewed_elf_linkage_and_node_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            build(directory / "helper")
            for lazy in (False, True):
                subprocess.run(
                    [
                        "cc",
                        "-std=gnu11",
                        "-O1",
                        "-fPIC",
                        "-shared",
                        "-Wall",
                        "-Wextra",
                        "-Werror",
                        "-Wl,--section-start=.dpe_show=0xe5fe50",
                        "-Wl,--section-start=.dpe_selection=0x22ecb40",
                        *(["-DLAZY_PICKER"] if lazy else []),
                        "-I" + str(node_headers()),
                        str(ROOT / "tests/screenshare/capture-picker-module.c"),
                        "-ldl",
                        "-pthread",
                        "-o",
                        str(directory / "discord_voice.node"),
                    ],
                    check=True,
                )
                result = subprocess.run(
                    [
                        "node",
                        str(ROOT / "tests/screenshare/capture-picker-native-check.cjs"),
                        str(directory),
                        str(directory / "helper/libdpe-camera-probe.so"),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("real TSFN events", result.stdout)

    def run_fixture(self, source, expected):
        rust, _ = build_rust(features=("picker-dispatch-fixture",))
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
                    "-I" + str(node_headers()),
                    str(ROOT / "tests/screenshare" / source),
                    str(rust),
                    "-Wl,--gc-sections",
                    "-lva",
                    "-lva-drm",
                    "-lm",
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
