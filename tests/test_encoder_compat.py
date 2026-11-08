"""Exercise Rust encoder ownership, native ELF installation and hardware scope."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import ROOT, sha256
from encoder_scope import scope


class EncoderCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        subprocess.run(
            ["cargo", "test", "--locked", "-p", "dpe-native", "--lib", "--no-run"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=180,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_native_create_resize_failure_and_abi(self):
        subprocess.run(
            ["cargo", "test", "--locked", "-p", "dpe-native", "encoder_guard::tests::"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=180,
        )

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
                subprocess.run(
                    [
                        "cargo",
                        "test",
                        "--locked",
                        "-p",
                        "dpe-native",
                        "encoder_guard::tests::hardware_scope_uses_every_amd_render_device",
                        "--",
                        "--ignored",
                        "--exact",
                    ],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    timeout=60,
                    env={
                        **os.environ,
                        "DPE_TEST_DRM_ROOT": str(root),
                        "DPE_TEST_EXPECT_VCN4": str(int(expected)),
                    },
                )

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
                # Exercise the reviewed classic PLT even on CET-default toolchains.
                "-fcf-protection=none",
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
        subprocess.run(
            [
                "cargo",
                "test",
                "--locked",
                "-p",
                "dpe-native",
                "encoder_install::tests::lazy_installer_checks_actual_slots_and_idempotence",
                "--",
                "--ignored",
                "--exact",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=60,
            env={
                **os.environ,
                "DPE_TEST_ENCODER_MODULE": str(module),
                "DPE_TEST_ENCODER_SCOPE": ",".join(arguments),
            },
        )


if __name__ == "__main__":
    unittest.main()
