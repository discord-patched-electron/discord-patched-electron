import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from patch_krisp import INITIALIZE, SIGNATURE, patch


@unittest.skipUnless(shutil.which("gcc"), "gcc is required for the ELF fixture")
class Krisp(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.binary = self.directory / "fixture.node"
        assembly = self.directory / "fixture.S"
        assembly.write_text(f"""
.text
.global {SIGNATURE}
.type {SIGNATURE}, @function
{SIGNATURE}:
    xor %eax, %eax
    ret
.size {SIGNATURE}, .-{SIGNATURE}
.global {INITIALIZE}
.type {INITIALIZE}, @function
{INITIALIZE}:
    call {SIGNATURE}
    test %eax, %eax
    je fail
    mov $1, %eax
    ret
.rept 160
    nop
.endr
fail:
    xor %eax, %eax
    ret
.size {INITIALIZE}, .-{INITIALIZE}
.section .note.GNU-stack,"",@progbits
""")
        subprocess.run(["gcc", "-shared", "-nostdlib", "-Wl,-Bsymbolic", str(assembly), "-o", str(self.binary)], check=True)

    def test_changes_only_checked_branch_and_is_idempotent(self):
        original = self.binary.read_bytes()
        report = patch(self.binary)
        modified = self.binary.read_bytes()
        offset, size = report["offset"], report["size"]
        self.assertEqual(modified, original[:offset] + b"\x90" * size + original[offset + size:])
        self.assertFalse(report["runtime_validated"])
        second = patch(self.binary)
        self.assertTrue(second["already_patched"])
        self.assertEqual(self.binary.read_bytes(), modified)

    def test_wrong_input_checksum_does_not_modify_binary(self):
        before = self.binary.read_bytes()
        with self.assertRaises(ValueError):
            patch(self.binary, "0" * 64)
        self.assertEqual(self.binary.read_bytes(), before)

    def test_stripped_module_fails_without_modification(self):
        subprocess.run(["strip", str(self.binary)], check=True)
        before = self.binary.read_bytes()
        with self.assertRaises(ValueError):
            patch(self.binary)
        self.assertEqual(self.binary.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
