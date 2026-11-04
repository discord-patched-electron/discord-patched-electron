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
from patch_pulse_audio import (
    ATTACH,
    BUFFER_OFFSET,
    BUFFER_SIZE,
    MIC_VOLUME,
    edits,
    patch,
    patch_site,
    volume_edits,
    volume_site,
)


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
            if name == MIC_VOLUME:
                calls = {
                    0x1A: "fixture_noop_log",
                    0x49: "fixture_noop_log",
                    0x5E: "fixture_table",
                    0x7C: "fixture_table",
                    0x8F: "fixture_table",
                    0xA1: "fixture_table",
                    0xC0: "fixture_wait",
                    0x104: "fixture_table",
                    0x11F: "fixture_table",
                    0x149: "fixture_table",
                    0x15F: "fixture_noop_log",
                    0x184: "fixture_noop_log",
                    0x191: "fixture_noop_log",
                    0x19A: "fixture_table",
                    0x1C5: "fixture_noop_log",
                    0x1CA: "fixture_table",
                }
                # The vector initializer's RIP-relative address needs real aligned data.
                raw = bytearray(raw)
            position = 0
            for at, target in sorted(calls.items()) if name in (ATTACH, MIC_VOLUME) else []:
                if name == MIC_VOLUME and position <= 0xCA < at:
                    assembly += [".byte " + ",".join(str(b) for b in raw[position:0xCA])]
                    assembly += ["movaps fixture_vector(%rip), %xmm0"]
                    position = 0xD1
                assembly += [".byte " + ",".join(str(b) for b in raw[position:at])]
                assembly += [f"call {target}"]
                position = at + 5
            assembly += [".byte " + ",".join(str(b) for b in raw[position:])]
            assembly += [f".size {name}, .-{name}"]
        assembly += [
            ".balign 16",
            "fixture_vector: .fill 16,1,0",
            '.section .note.GNU-stack,"",@progbits',
        ]
        source = self.directory / "fixture.S"
        source.write_text("\n".join(assembly))
        stubs = self.directory / "stubs.c"
        stubs.write_text(f"""
#include <assert.h>
#include <stdint.h>
static unsigned char *controller;
int volume_calls, set_calls;
static void *table[64];
static void *fixture_source(void *context, unsigned index, void *callback, void *data) {{
    (void)context; (void)index; (void)callback; (void)data; return (void*)1;
}}
static void *fixture_volume(void *out, unsigned channels, unsigned volume) {{
    assert(channels >= 1 && channels <= 32 && volume <= 0x7fffffffU);
    volume_calls++; return out;
}}
static void *fixture_set(void *a, unsigned b, void *c, void *d, void *e) {{
    (void)a; (void)b; (void)c; (void)d; (void)e; set_calls++; return (void*)1;
}}
void fixture_wait(void *a, void *b) {{ (void)a; (void)b; }}
void fixture_unref(void *a) {{ (void)a; }}
void pa_threaded_mainloop_lock(void *);
void pa_threaded_mainloop_unlock(void *);
void *fixture_table(void) {{
    table[0x60/8] = fixture_source; table[0xb8/8] = fixture_volume;
    table[0x98/8] = fixture_set; table[0xc8/8] = fixture_unref;
    table[0x190/8] = pa_threaded_mainloop_lock;
    table[0x1b8/8] = pa_threaded_mainloop_unlock; return table;
}}
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
        for relative, replacement in volume_edits():
            offset = report["volume_offset"] + relative
            expected[offset : offset + len(replacement)] = replacement
        self.assertEqual(self.binary.read_bytes(), expected)
        self.assertEqual(self.binary.stat().st_mode & 0o777, 0o751)
        self.assertFalse(report["already_patched"])
        self.assertTrue(patch(self.binary)["already_patched"])
        self.assertEqual(self.binary.read_bytes(), expected)

    def test_full_preparation_accepts_volume_guard_and_preserves_it_on_repeat(self):
        # Use the executable ELF Pulse fixture plus independent reviewed HRD and
        # dimension sites. Full-file identities include both Pulse corrections.
        import hashlib
        import os
        from unittest.mock import patch as mock_patch

        import patch_app_audio as audio
        import patch_capture_dimensions as dimensions
        import patch_decoder_eagain as decoder
        import patch_vaapi_hrd as hrd
        from common import ROOT

        initial = bytearray(self.binary.read_bytes())
        sites = []
        for _, before, after in hrd.SITES:
            sites.append((len(initial), before, after))
            initial.extend(before)
        dimension_offset = len(initial)
        initial.extend(dimensions.BEFORE)
        decoder_sites = []
        for _, before, after in decoder.SITES:
            decoder_sites.append((len(initial), before, after))
            initial.extend(before)
        initial.extend(b"unrelated-native-tail")
        self.binary.write_bytes(initial)
        patch(self.binary)
        guarded = self.binary.read_bytes()
        intermediate = bytearray(guarded)
        for offset, _, after in sites:
            intermediate[offset : offset + len(after)] = after
        final = bytearray(intermediate)
        final[dimension_offset : dimension_offset + len(dimensions.AFTER)] = dimensions.AFTER
        dimensions_final = bytes(final)
        for offset, _, after in decoder_sites:
            final[offset : offset + len(after)] = after
        self.binary.write_bytes(initial)
        shared = self.directory / "shared-original.node"
        os.link(self.binary, shared)
        native = self.directory / "discord_voice.node"
        self.binary.rename(native)
        wrapper = self.directory / "index.js"
        wrapper.write_text(
            audio.CAMERA_BEFORE
            + "\nVoiceEngine.initialize({});\n"
            + audio.ANCHOR
            + "    return instance;\n}\nmodule.exports = VoiceEngine;\n"
        )

        def digest(data):
            return hashlib.sha256(data).hexdigest()

        with (
            mock_patch("patch_app_audio.patch_capture_chroma", return_value={}),
            mock_patch.multiple(
                hrd,
                VOLUME_INPUT_SHA256=digest(guarded),
                VOLUME_OUTPUT_SHA256=digest(intermediate),
                SITES=tuple(sites),
            ),
            mock_patch.multiple(
                dimensions,
                VOLUME_INPUT_SHA256=digest(intermediate),
                VOLUME_OUTPUT_SHA256=digest(dimensions_final),
                OFFSET=dimension_offset,
            ),
            mock_patch.multiple(
                decoder,
                INPUT_SHA256=digest(dimensions_final),
                OUTPUT_SHA256=digest(final),
                SITES=tuple(decoder_sites),
            ),
        ):
            first = audio.patch(self.directory, ROOT / "integration/audio/app-audio.cjs")
            self.assertEqual(native.read_bytes(), final)
            self.assertEqual(shared.read_bytes(), initial)
            self.assertEqual(first["native_video_patch"]["input_sha256"], digest(guarded))
            self.assertTrue(volume_site(native.read_bytes())[1])
            inode = native.stat().st_ino
            second = audio.patch(self.directory, ROOT / "integration/audio/app-audio.cjs")
            self.assertTrue(second["native_buffer_patch"]["already_patched"])
            self.assertTrue(second["native_video_patch"]["already_patched"])
            self.assertTrue(second["native_capture_dimensions_patch"]["already_patched"])
            self.assertTrue(second["native_decoder_patch"]["already_patched"])
            self.assertEqual(native.stat().st_ino, inode)
            self.assertEqual(native.read_bytes(), final)
            tampered = bytes(final[:-1]) + b"!"
            native.write_bytes(tampered)
            with self.assertRaisesRegex(ValueError, "Unsupported H.264"):
                audio.patch(self.directory, ROOT / "integration/audio/app-audio.cjs")
            self.assertEqual(native.read_bytes(), tampered)

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

    def test_changed_or_partial_microphone_guard_fails_without_writing(self):
        original = self.binary.read_bytes()
        offset, _ = volume_site(original)
        for relative in (0xC8, 0x11B, 0x1D5):
            changed = bytearray(original)
            changed[offset + relative] ^= 1
            self.binary.write_bytes(changed)
            with self.assertRaisesRegex(ValueError, "microphone"):
                patch(self.binary)
            self.assertEqual(self.binary.read_bytes(), changed)
        changed = bytearray(original)
        at, replacement = volume_edits()[0]
        changed[offset + at : offset + at + len(replacement)] = replacement
        self.binary.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, "microphone"):
            patch(self.binary)
        self.assertEqual(self.binary.read_bytes(), changed)

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

    def test_hotplug_volume_guard_unlocks_and_preserves_valid_volumes(self):
        patch(self.binary)
        script = r"""
import ctypes, sys
lib = ctypes.CDLL(sys.argv[1])
method = getattr(lib, sys.argv[2])
method.argtypes = [ctypes.c_void_p, ctypes.c_uint]
method.restype = ctypes.c_int
state = ctypes.create_string_buffer(128)
base = ctypes.addressof(state)
ctypes.c_short.from_address(base + 2).value = 1
ctypes.c_void_p.from_address(base + 0x18).value = base
for channels in (0, 33, 255, 1, 2, 32):
    ctypes.c_ubyte.from_address(base + 0x3c).value = channels
    for volume in (0, 65536, 0x7fffffff, 0x80000000, 0xffffffff):
        valid = 1 <= channels <= 32 and volume <= 0x7fffffff
        calls = ctypes.c_int.in_dll(lib, 'volume_calls').value
        assert method(base, volume) == (0 if valid else -1)
        assert ctypes.c_int.in_dll(lib, 'volume_calls').value == calls + valid
        assert ctypes.c_int.in_dll(lib, 'locked').value == 0
        assert ctypes.c_int.in_dll(lib, 'lock_count').value == ctypes.c_int.in_dll(lib, 'unlock_count').value
"""
        subprocess.run(
            [sys.executable, "-c", script, str(self.binary), MIC_VOLUME],
            check=True,
            timeout=5,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
