"""Execute the native H.264 argument setup, without a GPU or a Discord process."""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import patch_capture_dimensions as dimensions
import patch_vaapi_hrd as hrd
from stage_vaapi_hrd_fix import stage


@contextmanager
def reviewed_fixture(root, *, pinned=False):
    sites = tuple((32 + i * 32, old, new) for i, (_, old, new) in enumerate(hrd.SITES))
    original = bytearray(b"\x5a" * 128)
    expected = bytearray(original)
    for offset, old, new in sites:
        original[offset : offset + len(old)] = old
        expected[offset : offset + len(new)] = new
    module = root / "discord_voice.node"
    module.write_bytes(original)
    prefix = "PINNED_" if pinned else ""
    with patch.multiple(
        hrd,
        **{
            prefix + "INPUT_SHA256": hashlib.sha256(original).hexdigest(),
            prefix + "OUTPUT_SHA256": hashlib.sha256(expected).hexdigest(),
            prefix + "SITES": sites,
        },
    ):
        yield module, bytes(original), bytes(expected)


@contextmanager
def reviewed_pipeline_fixture(root, *, pinned=False):
    sites = tuple((32 + i * 32, old, new) for i, (_, old, new) in enumerate(hrd.SITES))
    original = bytearray(b"\x5a" * 128)
    dimension_offset = 96
    original[dimension_offset : dimension_offset + len(dimensions.BEFORE)] = dimensions.BEFORE
    intermediate = bytearray(original)
    for offset, old, new in sites:
        original[offset : offset + len(old)] = old
        intermediate[offset : offset + len(new)] = new
    final = bytearray(intermediate)
    final[dimension_offset : dimension_offset + len(dimensions.AFTER)] = dimensions.AFTER
    module = root / "voice.node"
    module.write_bytes(original)
    prefix = "PINNED_" if pinned else ""
    with (
        patch.multiple(
            hrd,
            **{
                prefix + "INPUT_SHA256": hashlib.sha256(original).hexdigest(),
                prefix + "OUTPUT_SHA256": hashlib.sha256(intermediate).hexdigest(),
                prefix + "SITES": sites,
            },
        ),
        patch.multiple(
            dimensions,
            **{
                prefix + "INPUT_SHA256": hashlib.sha256(intermediate).hexdigest(),
                prefix + "OUTPUT_SHA256": hashlib.sha256(final).hexdigest(),
                prefix + "OFFSET": dimension_offset,
            },
        ),
    ):
        yield module, bytes(original), bytes(intermediate), bytes(final)


class VaapiHrdCorrection(unittest.TestCase):
    def test_each_reviewed_layout_selects_its_own_offsets_and_output(self):
        with tempfile.TemporaryDirectory() as directory:
            for pinned in (False, True):
                with (
                    self.subTest(pinned=pinned),
                    reviewed_fixture(Path(directory), pinned=pinned) as (module, _, expected),
                ):
                    report = hrd.patch(module)
                    self.assertEqual(module.read_bytes(), expected)
                    self.assertEqual(report["output_sha256"], hashlib.sha256(expected).hexdigest())
                    self.assertEqual(report["changed_bytes"], 8)
                    self.assertTrue(hrd.patch(module)["already_patched"])

    def test_preparation_is_atomic_idempotent_and_preserves_shared_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with reviewed_fixture(root) as (module, original, expected):
                module.chmod(0o751)
                shared = root / "source.node"
                os.link(module, shared)
                report = hrd.patch(module)
                self.assertEqual(module.read_bytes(), expected)
                self.assertEqual(shared.read_bytes(), original)
                self.assertEqual(module.stat().st_mode & 0o777, 0o751)
                self.assertEqual(report["changed_bytes"], 8)
                self.assertFalse(report["hardware_performance_validated"])
                inode = module.stat().st_ino
                report = hrd.patch(module)
                self.assertTrue(report["already_patched"])
                self.assertEqual(report["changed_bytes"], 0)
                self.assertEqual(module.stat().st_ino, inode)

    def test_failed_write_validation_or_replacement_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            with reviewed_fixture(Path(directory)) as (module, original, expected):
                with patch.object(Path, "replace", side_effect=OSError("fixture failure")):
                    with self.assertRaises(OSError):
                        hrd.patch(module)
                self.assertEqual(module.read_bytes(), original)
                with patch.object(hrd, "corrected", return_value=expected + b"corrupt"):
                    with self.assertRaisesRegex(ValueError, "written output failed"):
                        hrd.patch(module)
                self.assertEqual(module.read_bytes(), original)

    def test_unknown_and_tampered_modules_fail_without_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            with reviewed_fixture(Path(directory)) as (module, original, expected):
                for contents in (b"unknown", original[:-1] + b"!", expected[:-1] + b"!"):
                    module.write_bytes(contents)
                    with self.assertRaisesRegex(ValueError, "Unsupported H.264"):
                        hrd.patch(module)
                    self.assertEqual(module.read_bytes(), contents)

    @unittest.skipUnless(
        platform.machine() == "x86_64" and shutil.which("cc"), "x86_64 C compiler required"
    )
    def test_native_callers_pass_fullness_then_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assembly = [".text"]
            for index, (_, before, after) in enumerate(hrd.SITES):
                for state, instructions in (("before", before), ("after", after)):
                    name = f"evaluate_{index}_{state}"
                    assembly += [f".globl {name}", f".type {name}, @function", f"{name}:"]
                    # Recreate each native caller's base register. Execute the
                    # actual reviewed load/LEA/SHR bytes, not a Python rewrite.
                    assembly += ["push %rbp", "mov %rdi, %rax", "mov %rdi, %rbp"]
                    assembly += [".byte " + ",".join(hex(byte) for byte in instructions)]
                    # SysV arguments: EDI=fullness, ESI=capacity. Return both to
                    # inspect the arguments the real cros-libva constructor gets.
                    assembly += [
                        "mov %esi, %eax",
                        "shl $32, %rax",
                        "mov %edi, %edx",
                        "or %rdx, %rax",
                        "pop %rbp",
                        "ret",
                    ]
            assembly += ['.section .note.GNU-stack,"",@progbits']
            source = root / "callers.S"
            source.write_text("\n".join(assembly) + "\n")
            library = root / "callers.so"
            subprocess.run(["cc", "-shared", "-fPIC", str(source), "-o", str(library)], check=True)
            # A separate process contains any fault in extracted machine code.
            program = """
import ctypes, struct, sys
library = ctypes.CDLL(sys.argv[1])
for index, offset in enumerate((0x38c, 0x3ec)):
    data = ctypes.create_string_buffer(b'\\x5a' * 2048)
    # Exercise changing buffer sizes and non-multiples of four. These are
    # configuration/ABI tests, not hardware rate-control simulations.
    for capacity in (1000000, 400000, 133333, 4000000, 17, 0):
        struct.pack_into('<I', data, offset, capacity)
        unchanged = data.raw
        for state in ('before', 'after'):
            function = getattr(library, f'evaluate_{index}_{state}')
            function.argtypes = [ctypes.c_void_p]
            function.restype = ctypes.c_uint64
            value = function(ctypes.addressof(data))
            fullness, size = value & 0xffffffff, value >> 32
            if state == 'before':
                assert (fullness, size) == (capacity, capacity * 3 // 4)
                assert not capacity or fullness > size
            else:
                assert (fullness, size) == (capacity * 3 // 4, capacity)
                assert fullness <= size
            assert data.raw == unchanged
"""
            subprocess.run([sys.executable, "-c", program, str(library)], check=True, timeout=10)

    def test_unknown_modules_never_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module, output = root / "voice.node", root / "candidate"
            module.write_bytes(b"unknown module")
            with self.assertRaisesRegex(ValueError, "Unsupported H.264"):
                stage(module, output)
            self.assertEqual(module.read_bytes(), b"unknown module")
            self.assertFalse(output.exists())

    def test_staging_changes_only_reviewed_bytes_and_preserves_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "candidate"
            with reviewed_pipeline_fixture(root) as (module, original, intermediate, expected):
                module.chmod(0o751)
                module.with_name("libmediapipe.so").write_bytes(b"fixture dependency")
                helper = module.with_name("gpu_encoder_helper")
                helper.write_bytes(b"fixture GPU probe")
                helper.chmod(0o755)
                destination = stage(module, output)
                self.assertEqual(destination.read_bytes(), expected)
                self.assertEqual(module.read_bytes(), original)
                self.assertEqual(destination.stat().st_mode & 0o777, 0o751)
                self.assertEqual((output / "libmediapipe.so").read_bytes(), b"fixture dependency")
                self.assertEqual((output / "gpu_encoder_helper").read_bytes(), b"fixture GPU probe")
                self.assertEqual((output / "gpu_encoder_helper").stat().st_mode & 0o777, 0o755)
                report = json.loads((output / "hrd-correction.json").read_text())
                self.assertEqual(report["changed_bytes"], 8)
                self.assertEqual(report["output_sha256"], hashlib.sha256(intermediate).hexdigest())
                self.assertFalse(report["installed"])
                report = json.loads((output / "capture-dimensions-correction.json").read_text())
                self.assertEqual(report["changed_bytes"], 4)
                self.assertEqual(report["output_sha256"], hashlib.sha256(expected).hexdigest())
                with self.assertRaisesRegex(ValueError, "new candidate directory"):
                    stage(module, output)
                self.assertEqual(destination.read_bytes(), expected)

    def test_preparing_each_intermediate_state_is_order_safe_and_idempotent(self):
        for pinned in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with reviewed_pipeline_fixture(root, pinned=pinned) as fixture:
                    module, original, intermediate, final = fixture
                    for state in (original, intermediate, final):
                        with self.subTest(pinned=pinned, digest=hashlib.sha256(state).hexdigest()):
                            module.write_bytes(state)
                            hrd.patch(module)
                            dimensions.patch(module)
                            self.assertEqual(module.read_bytes(), final)
                            inode = module.stat().st_ino
                            self.assertTrue(hrd.patch(module)["already_patched"])
                            self.assertTrue(dimensions.patch(module)["already_patched"])
                            self.assertEqual(module.stat().st_ino, inode)
                    destination = stage(module, root / "candidate")
                    self.assertEqual(destination.read_bytes(), final)
                    # A dimensions-only partial edit must not become a trusted
                    # fully prepared state through either patch entry point.
                    malformed = original[:96] + dimensions.AFTER + original[108:]
                    module.write_bytes(malformed)
                    for apply in (hrd.patch, dimensions.patch):
                        with self.assertRaisesRegex(ValueError, "Unsupported"):
                            apply(module)
                        self.assertEqual(module.read_bytes(), malformed)

    def test_rejected_second_stage_creates_no_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with reviewed_pipeline_fixture(root) as (module, original, _, _):
                with patch.object(dimensions, "OUTPUT_SHA256", "0" * 64):
                    with self.assertRaisesRegex(ValueError, "reviewed output"):
                        stage(module, root / "candidate")
                self.assertFalse((root / "candidate").exists())
                self.assertEqual(module.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
