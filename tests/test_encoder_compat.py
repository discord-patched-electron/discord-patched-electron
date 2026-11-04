"""Exercise the real C guard, by-value ABI forwarding and hardware scope."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_camera_support import node_headers
from common import ROOT, sha256
from encoder_scope import scope


class EncoderCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.binary = cls.root / "encoder-check"
        subprocess.run(
            [
                "cc",
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I" + str(node_headers()),
                str(ROOT / "tests/screenshare/encoder-check.c"),
                str(ROOT / "integration/video/encoder-hevc.c"),
                "-ldl",
                "-pthread",
                "-o",
                str(cls.binary),
            ],
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_native_create_resize_failure_and_abi(self):
        subprocess.run([str(self.binary)], check=True, capture_output=True)

    def test_sysfs_generation_scope(self):
        for index, (devices, expected) in enumerate(
            [
                ([], False),
                ([(0x1002, [4])], True),
                ([(0x1002, [4, 4]), (0x8086, [])], True),
                ([(0x1002, [4]), (0x1002, [4])], True),
                ([(0x1002, [4]), (0x1002, [5])], False),
                ([(0x1002, [])], False),
                ([(0x1002, [5])], False),
                ([(0x8086, [4])], False),
                ([(0x10DE, [])], False),
                ([(0x1002, [4]), (0x10DE, []), (0x8086, [])], True),
            ]
        ):
            with self.subTest(devices=devices):
                root = self.root / f"sysfs-{index}"
                root.mkdir()
                for device, (vendor, versions) in enumerate(devices):
                    path = root / f"renderD{128 + device}" / "device"
                    path.mkdir(parents=True)
                    (path / "vendor").write_text(hex(vendor))
                    for instance, version in enumerate(versions):
                        major = path / f"ip_discovery/die/0/UVD/{instance}/major"
                        major.parent.mkdir(parents=True)
                        major.write_text(str(version))
                subprocess.run([str(self.binary), str(root), str(int(expected))], check=True)

    def test_unknown_native_module_is_not_patched(self):
        module = self.root / "unknown.node"
        module.write_bytes(b"unknown native module")
        self.assertEqual(scope(module)["reason"], "unreviewed-native-module")

    def test_installs_over_real_lazy_plt_bindings(self):
        module = self.root / "encoder-linkage.so"
        subprocess.run(
            [
                "cc",
                "-shared",
                "-fPIC",
                "-Wl,-z,lazy",
                "-o",
                str(module),
                str(ROOT / "tests/screenshare/encoder-linkage.c"),
            ],
            check=True,
        )
        with (
            patch("encoder_scope.REVIEWED", {sha256(module)}),
            patch("encoder_scope.sequence_scope", return_value={"target": 1, "calls": [2, 3]}),
            patch("encoder_scope.quality_scope", return_value={"slot": 4, "target": 5, "query": 6}),
            patch("encoder_scope.qp_trust_scope", return_value={"slot": 7, "target": 8}),
            patch("encoder_scope.transition_scope", return_value={}),
            patch("encoder_scope.allocation_scope", return_value={}),
            patch("encoder_scope.stream_limit_scope", return_value={}),
            patch("encoder_scope.hevc_scope", return_value=None),
        ):
            metadata = scope(module)
        arguments = []
        for name in ("create", "update", "destroy"):
            arguments.extend(str(metadata[name][field]) for field in ("slot", "target", "lazy"))
        arguments.append(str(metadata["vendor"]["target"]))
        subprocess.run([str(self.binary), str(module), *arguments], check=True)


if __name__ == "__main__":
    unittest.main()
