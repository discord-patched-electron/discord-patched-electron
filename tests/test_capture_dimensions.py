"""Verify reviewed native dimension instructions and fail-closed preparation."""

import hashlib
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


@contextmanager
def reviewed_fixture(root, *, pinned=False):
    offset = 32 if pinned else 64
    original = b"\x5a" * offset + dimensions.BEFORE + b"\x5a" * 32
    expected = original[:offset] + dimensions.AFTER + original[offset + len(dimensions.BEFORE) :]
    module = root / "discord_voice.node"
    module.write_bytes(original)
    prefix = "PINNED_" if pinned else ""
    with patch.multiple(
        dimensions,
        **{
            prefix + "INPUT_SHA256": hashlib.sha256(original).hexdigest(),
            prefix + "OUTPUT_SHA256": hashlib.sha256(expected).hexdigest(),
            prefix + "OFFSET": offset,
        },
    ):
        yield module, original, expected


class CaptureDimensions(unittest.TestCase):
    def test_reviewed_layouts_patch_atomically_preserving_shared_source(self):
        for pinned in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.subTest(pinned=pinned), reviewed_fixture(root, pinned=pinned) as fixture:
                    module, original, expected = fixture
                    module.chmod(0o751)
                    shared = root / "shared.node"
                    os.link(module, shared)
                    report = dimensions.patch(module)
                    self.assertEqual(module.read_bytes(), expected)
                    self.assertEqual(shared.read_bytes(), original)
                    self.assertEqual(module.stat().st_mode & 0o777, 0o751)
                    self.assertEqual(report["changed_bytes"], 4)
                    self.assertEqual(report["output_sha256"], hashlib.sha256(expected).hexdigest())
                    inode = module.stat().st_ino
                    report = dimensions.patch(module)
                    self.assertTrue(report["already_patched"])
                    self.assertEqual(report["changed_bytes"], 0)
                    self.assertEqual(module.stat().st_ino, inode)

    def test_unknown_tampered_and_wrong_patch_order_modules_are_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            with reviewed_fixture(Path(directory)) as (module, original, expected):
                for data in (b"unknown", original[:-1] + b"!", expected[:-1] + b"!"):
                    module.write_bytes(data)
                    with self.assertRaisesRegex(ValueError, "Unsupported GPU capture module"):
                        dimensions.patch(module)
                    self.assertEqual(module.read_bytes(), data)
        # This correction deliberately follows HRD; uncorrected upstream files
        # do not pass just because their dimension instructions look identical.
        from patch_vaapi_hrd import INPUT_SHA256, PINNED_INPUT_SHA256

        for digest in (INPUT_SHA256, PINNED_INPUT_SHA256):
            with self.assertRaisesRegex(ValueError, "Unsupported GPU capture module"):
                dimensions.reviewed(digest)

    def test_corrupt_site_output_or_failed_replace_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            with reviewed_fixture(Path(directory)) as (module, original, expected):
                with patch.object(dimensions, "BEFORE", b"mismatch"):
                    with self.assertRaisesRegex(ValueError, "instructions differ"):
                        dimensions.patch(module)
                with patch.object(dimensions, "OUTPUT_SHA256", "0" * 64):
                    with self.assertRaisesRegex(ValueError, "reviewed output"):
                        dimensions.patch(module)
                with patch.object(dimensions, "corrected", return_value=expected + b"corrupt"):
                    with self.assertRaisesRegex(ValueError, "written output failed"):
                        dimensions.patch(module)
                with patch.object(Path, "replace", side_effect=OSError("fixture failure")):
                    with self.assertRaises(OSError):
                        dimensions.patch(module)
                self.assertEqual(module.read_bytes(), original)

    @unittest.skipUnless(
        platform.machine() == "x86_64" and shutil.which("cc"), "x86_64 C compiler required"
    )
    def test_native_rounding_preserves_even_dimensions_and_bounds_odd_adjustment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assembly = [".text"]
            for state, instructions in (("before", dimensions.BEFORE), ("after", dimensions.AFTER)):
                name = f"evaluate_{state}"
                assembly += [f".globl {name}", f".type {name}, @function", f"{name}:"]
                # Execute the actual reviewed native bytes with both original
                # registers, then pack the pair for the isolated test process.
                assembly += ["mov %edi, %eax", "mov %esi, %ecx"]
                assembly += [".byte " + ",".join(hex(byte) for byte in instructions)]
                assembly += ["shl $32, %rcx", "or %rcx, %rax", "ret"]
            assembly += ['.section .note.GNU-stack,"",@progbits']
            source = root / "dimensions.S"
            source.write_text("\n".join(assembly) + "\n")
            library = root / "dimensions.so"
            subprocess.run(["cc", "-shared", "-fPIC", str(source), "-o", str(library)], check=True)
            program = """
import ctypes, sys
library = ctypes.CDLL(sys.argv[1])
for state in ('before', 'after'):
    fn = getattr(library, f'evaluate_{state}')
    fn.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    fn.restype = ctypes.c_uint64
def evaluate(state, width, height):
    result = getattr(library, f'evaluate_{state}')(width, height)
    return result & 0xffffffff, result >> 32
assert evaluate('before', 1920, 1080) == (1920, 1088)
assert evaluate('before', 1280, 720) == (1280, 704)
assert evaluate('before', 640, 360) == (640, 384)
for size in ((1920,1080), (1280,720), (640,360), (2560,1440), (3840,2160),
             (4096,2160), (1080,1920), (3440,1440), (2560,1080)):
    assert evaluate('after', *size) == size
# Native aspect fit limits each dimension to 4096 before this instruction site.
# Exercise the complete bounded domain, including odd window sizes.
for width in range(4097):
    height = 4096-width
    result = evaluate('after', width, height)
    assert result == ((width+1)&~1, (height+1)&~1)
    assert all(0 <= value <= 4096 and value%2 == 0 for value in result)
    assert result[0]-width <= 1 and result[1]-height <= 1
"""
            subprocess.run([sys.executable, "-c", program, str(library)], check=True, timeout=10)
