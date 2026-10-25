"""Execute the reviewed Attach instructions with deterministic PulseAudio stubs.

The fixture contains the normalized controller instructions from the locked
Canary module (d7937236a8bc448aebe68b9a2819eb255d45e0457a4adaf80582d4f5fc3045a5).
Other controller methods are layout evidence only and are never executed here.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from patch_pulse_audio import ATTACH, BUFFER_OFFSET, BUFFER_SIZE, edits, patch, patch_site


@unittest.skipUnless(shutil.which("gcc"), "gcc is required for the ELF fixture")
class PulseAudioPatch(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.binary = self.directory / "fixture.node"
        functions = json.loads(
            (Path(__file__).parent / "fixtures/pulse-controller.json").read_text()
        )
        assembly = [".text"]
        for name, code in functions.items():
            assembly += [f".global {name}", f".type {name}, @function", f"{name}:"]
            raw = bytes.fromhex(code)
            # Relocate only the Attach calls we exercise. Skip native logging and
            # return a not-yet-ready context after checking the locked buffer.
            calls = {
                0x11: "fixture_connect",
                0x22: "fixture_noop_log",
                0x60: "pa_threaded_mainloop_lock",
                0x79: "fixture_context_state",
                0xDA: "pa_threaded_mainloop_unlock",
            }
            position = 0
            for at, target in calls.items() if name == ATTACH else []:
                assembly += [".byte " + ",".join(str(b) for b in raw[position:at])]
                assembly += [f"call {target}"]
                position = at + 5
            assembly += [".byte " + ",".join(str(b) for b in raw[position:])]
            assembly += [f".size {name}, .-{name}"]
        assembly += ['.section .note.GNU-stack,"",@progbits']
        source = self.directory / "fixture.S"
        source.write_text("\n".join(assembly))
        stubs = self.directory / "stubs.c"
        stubs.write_text(f"""
static unsigned char *controller;
int lock_count, unlock_count, locked, saw_zero_while_locked;
void fixture_set_controller(void *p) {{ controller = p; }}
void fixture_connect(void) {{}}
int fixture_noop_log(void) {{ return 1; }}
void pa_threaded_mainloop_lock(void *p) {{ (void)p; lock_count++; locked = 1; }}
void pa_threaded_mainloop_unlock(void *p) {{ (void)p; unlock_count++; locked = 0; }}
int fixture_context_state(void *p) {{
    (void)p;
    int zero = 1;
    for (unsigned i = {BUFFER_OFFSET}; i < {BUFFER_OFFSET + BUFFER_SIZE}; i++)
        if (controller[i]) zero = 0;
    saw_zero_while_locked = locked && zero;
    return 0;
}}
""")
        subprocess.run(
            [
                "gcc",
                "-shared",
                "-fPIC",
                "-Wl,-Bsymbolic",
                str(source),
                str(stubs),
                "-o",
                str(self.binary),
            ],
            check=True,
            capture_output=True,
        )

    def execute(self):
        # Isolate native execution so a broken trampoline fails this test rather
        # than taking down the test runner. No audio server or device is opened.
        script = f"""
import ctypes, json, sys
lib = ctypes.CDLL(sys.argv[1])
data = ctypes.create_string_buffer(b'\\x5a' * 0x30000)
base = ctypes.addressof(data)
lib.fixture_set_controller.argtypes = [ctypes.c_void_p]
lib.fixture_set_controller(base)
attach = getattr(lib, {ATTACH!r})
attach.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p]
attach.restype = ctypes.c_bool
def counter(name): return ctypes.c_int.in_dll(lib, name).value
pid = ctypes.c_uint.from_address(base + 0x280)
pid.value = 42
before = data.raw
assert attach(base, 99, None)
after = data.raw
zero = after[{BUFFER_OFFSET}:{BUFFER_OFFSET + BUFFER_SIZE}] == bytes({BUFFER_SIZE})
locked_zero = counter('saw_zero_while_locked')
assert pid.value == 99
assert after[:0x280] == before[:0x280]
assert after[0x285:{BUFFER_OFFSET}] == before[0x285:{BUFFER_OFFSET}]
assert after[{BUFFER_OFFSET + BUFFER_SIZE}:] == before[{BUFFER_OFFSET + BUFFER_SIZE}:]
assert counter('lock_count') == counter('unlock_count') == 1
ctypes.memset(base + {BUFFER_OFFSET}, 0x7b, {BUFFER_SIZE})
retained = data.raw
assert attach(base, 99, None) and data.raw == retained
assert attach(base, 0, None) and data.raw == retained
assert counter('lock_count') == counter('unlock_count') == 1
assert attach(base, 100, None)
assert counter('lock_count') == counter('unlock_count') == 2
assert not counter('locked')
again = data.raw[{BUFFER_OFFSET}:{BUFFER_OFFSET + BUFFER_SIZE}] == bytes({BUFFER_SIZE})
print(json.dumps(dict(zero=zero, locked_zero=bool(locked_zero), again=again)))
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.binary)],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def test_reset_executes_under_lock_and_preserves_surrounding_state(self):
        self.assertEqual(self.execute(), {"zero": False, "locked_zero": False, "again": False})
        patch(self.binary)
        self.assertEqual(self.execute(), {"zero": True, "locked_zero": True, "again": True})

    def test_only_reviewed_spans_change_and_patch_is_idempotent(self):
        original = self.binary.read_bytes()
        self.binary.chmod(0o751)
        report = patch(self.binary)
        expected = bytearray(original)
        for relative, replacement in edits():
            offset = report["offset"] + relative
            expected[offset : offset + len(replacement)] = replacement
        self.assertEqual(self.binary.read_bytes(), expected)
        self.assertEqual(self.binary.stat().st_mode & 0o777, 0o751)
        self.assertFalse(report["already_patched"])
        self.assertTrue(patch(self.binary)["already_patched"])
        self.assertEqual(self.binary.read_bytes(), expected)

    def test_changed_instructions_lock_target_or_partial_patch_fail_unchanged(self):
        original = self.binary.read_bytes()
        offset, _ = patch_site(original)
        for relative in (0x18, 0x61, 0xDB, 0x27):
            with self.subTest(relative=relative):
                modified = bytearray(original)
                modified[offset + relative] ^= 1
                self.binary.write_bytes(modified)
                with self.assertRaises(ValueError):
                    patch(self.binary)
                self.assertEqual(self.binary.read_bytes(), modified)
        modified = bytearray(original)
        relative, replacement = edits()[0]
        modified[offset + relative : offset + relative + len(replacement)] = replacement
        self.binary.write_bytes(modified)
        with self.assertRaises(ValueError):
            patch(self.binary)
        self.assertEqual(self.binary.read_bytes(), modified)

    def test_changed_buffer_layout_fails_unchanged(self):
        original = self.binary.read_bytes()
        # Change the Read method's first buffer access while retaining Attach.
        marker = bytes.fromhex("86020000")
        offset = original.index(marker)
        changed = original[:offset] + b"\x88" + original[offset + 1 :]
        self.binary.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, "layout"):
            patch(self.binary)
        self.assertEqual(self.binary.read_bytes(), changed)

    def test_stripped_module_fails_unchanged(self):
        subprocess.run(["strip", str(self.binary)], check=True)
        original = self.binary.read_bytes()
        with self.assertRaises(ValueError):
            patch(self.binary)
        self.assertEqual(self.binary.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
