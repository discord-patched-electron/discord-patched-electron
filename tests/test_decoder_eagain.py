"""Execute the reviewed decoder branch bytes and verify exact-module patching."""

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import patch_decoder_eagain as decoder
from capstone import CS_ARCH_X86, CS_MODE_64, Cs
from native_client_2444 import STAGES
from native_client_2472 import STAGES as CLIENT_2472_STAGES
from native_client_2483 import STAGES as CLIENT_2483_STAGES


class DecoderBufferedOutput(unittest.TestCase):
    def test_atomic_exact_hash_patch_and_rejection(self):
        for sites, prefix in ((decoder.SITES, ""), (decoder.PINNED_SITES, "PINNED_")):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                data = bytearray(b"fixture-start")
                fixture_sites = []
                for _, before, after in sites:
                    fixture_sites.append((len(data), before, after))
                    data.extend(before)
                data.extend(b"unrelated-tail")
                expected = bytearray(data)
                for offset, before, after in fixture_sites:
                    expected[offset : offset + len(before)] = after
                original = bytes(data)
                module = root / "discord_voice.node"
                module.write_bytes(original)
                module.chmod(0o751)
                shared = root / "shared.node"
                os.link(module, shared)
                with patch.multiple(
                    decoder,
                    **{
                        prefix + "INPUT_SHA256": hashlib.sha256(original).hexdigest(),
                        prefix + "OUTPUT_SHA256": hashlib.sha256(expected).hexdigest(),
                        prefix + "SITES": tuple(fixture_sites),
                    },
                ):
                    self.assertFalse(decoder.patch(module)["already_patched"])
                    self.assertEqual(module.read_bytes(), expected)
                    self.assertEqual(module.stat().st_mode & 0o777, 0o751)
                    self.assertEqual(shared.read_bytes(), original)
                    inode = module.stat().st_ino
                    self.assertTrue(decoder.patch(module)["already_patched"])
                    self.assertEqual(module.stat().st_ino, inode)
                    for bad in (original[:-1] + b"!", bytes(expected[:-1]) + b"!", b"unknown"):
                        module.write_bytes(bad)
                        with self.assertRaisesRegex(ValueError, "Unsupported buffered decoder"):
                            decoder.patch(module)
                        self.assertEqual(module.read_bytes(), bad)

    @unittest.skipUnless(
        platform.machine() == "x86_64" and shutil.which("cc"), "x86_64 cc required"
    )
    def test_actual_branch_bytes_preserve_errors_and_only_accept_receive_eagain(self):
        # Rebase only external relocations to instrumented stubs. Execute each
        # original/replacement instruction, retaining internal branch bytes.
        assembly = [".text"]
        for version, sites in (
            ("old", decoder.SITES),
            ("pinned", decoder.PINNED_SITES),
            ("current", STAGES["decoder"][2]),
            ("client2472", CLIENT_2472_STAGES["decoder"][2]),
            ("latest", CLIENT_2483_STAGES["decoder"][2]),
        ):
            start = sites[1][0] - 0x1A9
            original = sites[1][1]
            targets = {}
            for ins in Cs(CS_ARCH_X86, CS_MODE_64).disasm(original, start + 0x1A9):
                if ins.mnemonic == "call":
                    target = int(ins.op_str, 16)
                    targets[target] = "log_stub" if ins.address - start == 0x202 else "noop_stub"
                elif ins.mnemonic == "lea":
                    displacement = int.from_bytes(ins.bytes[-4:], "little", signed=True)
                    target = ins.address + ins.size + displacement
                    targets[target] = {
                        0x1B8: "meta",
                        0x1BF: "filename",
                        0x1C6: "send_message",
                        0x1E3: "meta",
                        0x1EA: "filename",
                        0x1F1: "receive_message",
                    }[ins.address - start]
            for state, code, receive in (
                ("before", original, 0x1D4),
                ("after", sites[1][2], 0x1C6),
            ):
                name = f"{version}_{state}"
                local_targets = targets | {
                    start + 0x282: name + "_error",
                    start + 0x290: name + "_cleanup",
                }
                assembly += [
                    f".globl {name}",
                    f"{name}:",
                    "push %rbp",
                    "push %r15",
                    "sub $8,%rsp",
                    "mov %edi,%eax",
                    "test %esi,%esi",
                    f"jnz {name}_receive",
                    f"jmp {name}_send",
                    f"{name}_send:",
                ]
                for ins in Cs(CS_ARCH_X86, CS_MODE_64).disasm(code, start + 0x1A9):
                    if ins.address == start + receive:
                        assembly.append(f"{name}_receive:")
                    raw = bytes(ins.bytes)
                    target = None
                    if ins.mnemonic in ("call", "jne", "jmp"):
                        target = int(ins.op_str, 16)
                    elif ins.mnemonic == "lea":
                        target = (
                            ins.address + ins.size + int.from_bytes(raw[-4:], "little", signed=True)
                            if ins.size == 7
                            else None
                        )
                    if target in local_targets:
                        size = 1 if ins.size == 2 else 4
                        assembly.append(".byte " + ",".join(hex(b) for b in raw[:-size]))
                        assembly.append(
                            (".byte " if size == 1 else ".long ")
                            + f"{local_targets[target]} - . - {size}"
                        )
                    else:
                        assembly.append(".byte " + ",".join(hex(b) for b in raw))
                assembly += [
                    f"{name}_error:",
                    "incl errors(%rip)",
                    "mov $-1,%r15d",
                    f"{name}_cleanup:",
                    "incl cleanups(%rip)",
                    "mov %r15d,%eax",
                    "add $8,%rsp",
                    "pop %r15",
                    "pop %rbp",
                    "ret",
                ]
        assembly += ['.section .note.GNU-stack,"",@progbits']
        c = r"""
#include <assert.h>
int errors, cleanups, logs, quiet, saved_code, saved_line;
char meta, filename, send_message, receive_message;
int noop_stub(void) { return quiet; }
void log_stub(void *m, void *f, int line, void *message, int code) {
  assert(m == &meta && f == &filename);
  assert(message == (line == 0x127b ? &send_message : &receive_message));
  logs++; saved_code=code; saved_line=line;
}
int old_before(int,int), old_after(int,int), pinned_before(int,int), pinned_after(int,int), current_before(int,int), current_after(int,int);
int main(void) {
  int (*functions[])(int,int) = {old_before,old_after,pinned_before,pinned_after,current_before,current_after};
  int values[] = {-11,-1,-22,-12,-541478725,-1094995529};
  for(int fn=0;fn<6;fn++) for(int recv=0;recv<2;recv++) for(int q=0;q<2;q++)
    for(unsigned v=0;v<sizeof(values)/sizeof(values[0]);v++) {
      errors=cleanups=logs=0; quiet=q;
      int buffered = (fn%2) && recv && values[v]==-11;
      assert(functions[fn](values[v],recv)==(buffered?1:-1));
      assert(cleanups==1 && errors==!buffered && logs==(!buffered && !quiet));
      if(logs) { assert(saved_code==values[v]); assert(saved_line==(recv?0x12b3:0x127b)); }
    }
}
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "branch.S").write_text("\n".join(assembly) + "\n")
            (root / "main.c").write_text(c)
            subprocess.run(
                [
                    "cc",
                    "-no-pie",
                    str(root / "main.c"),
                    str(root / "branch.S"),
                    "-o",
                    str(root / "check"),
                ],
                check=True,
            )
            subprocess.run([str(root / "check")], check=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
