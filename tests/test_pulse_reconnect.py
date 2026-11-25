"""Execute the reconnect instructions with bounded, deterministic libpulse stubs."""

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
import patch_pulse_reconnect as reconnect
from capstone import CS_ARCH_X86, CS_MODE_64, Cs
from capstone.x86 import X86_OP_MEM, X86_REG_RIP


class PulseReconnect(unittest.TestCase):
    def test_exact_hash_atomic_patch_and_idempotent_layout_restoration(self):
        data = b"prefix" + b"original-code" + b"tail"
        result = b"prefix" + b"repaired-code" + b"tail"
        sites = ((6, b"original-code", b"repaired-code"),)
        with tempfile.TemporaryDirectory() as temporary:
            module = Path(temporary) / "voice.node"
            module.write_bytes(data)
            shared = Path(temporary) / "shared.node"
            os.link(module, shared)
            with patch.multiple(
                reconnect,
                INPUT_SHA256=hashlib.sha256(data).hexdigest(),
                OUTPUT_SHA256=hashlib.sha256(result).hexdigest(),
                SITES=sites,
            ):
                self.assertFalse(reconnect.patch(module)["already_patched"])
                self.assertEqual(module.read_bytes(), result)
                self.assertEqual(shared.read_bytes(), data)
                inode = module.stat().st_ino
                self.assertTrue(reconnect.patch(module)["already_patched"])
                self.assertEqual(module.stat().st_ino, inode)
                self.assertEqual(reconnect.restored(result), data)
                for bad in (b"unknown", data + b"!", result + b"!"):
                    module.write_bytes(bad)
                    with self.assertRaisesRegex(ValueError, "Unsupported Pulse reconnect"):
                        reconnect.patch(module)
                    self.assertEqual(module.read_bytes(), bad)

    @unittest.skipUnless(
        platform.machine() == "x86_64" and shutil.which("cc"), "x86_64 cc required"
    )
    def test_actual_instructions_reuse_live_contexts_and_rebuild_failed_contexts_under_lock(self):
        # Preserve every instruction and local branch; relocate only reviewed
        # native calls and constant strings to instrumented C stubs.
        targets = {
            0x291FE0: "loop_new",
            0x2906F0: "loop_api",
            0x290B20: "loop_lock",
            0x290230: "loop_unlock",
            0x292260: "loop_start",
            0x291530: "loop_name",
            0x2900F0: "ctx_new",
            0x292AD0: "ctx_state",
            0x292EE0: "ctx_unref",
            0x290820: "ctx_set_state",
            0x2907F0: "ctx_subscribe",
            0x292C60: "ctx_connect",
            0x472320: "callback_state",
            0x471630: "callback_event",
            0x472E70: "detach",
            0x185B117: "client_name",
            0x185B127: "thread_name",
            0x471E25: "duplicate_done",
        }
        relocated = {
            **targets,
            0x473CB0: "callback_state",
            0x472FC0: "callback_event",
            0x474800: "detach",
            0x185D117: "client_name",
            0x185D127: "thread_name",
            0x4737B5: "duplicate_done",
        }
        latest = {
            2695120: "loop_new",
            25563767: "thread_name",
            2692400: "loop_name",
            2695776: "loop_start",
            2689824: "loop_lock",
            2697968: "ctx_state",
            4685744: "detach",
            2699008: "ctx_unref",
            2688752: "loop_api",
            25563751: "client_name",
            2687216: "ctx_new",
            4682848: "callback_state",
            2689056: "ctx_set_state",
            4679536: "callback_event",
            2689008: "ctx_subscribe",
            2698368: "ctx_connect",
            2687536: "loop_unlock",
        }
        for version, sites, bindings in (
            ("2483", reconnect.SITES, targets),
            ("2484", reconnect.CLIENT_2484_STAGES["pulse-reconnect"][2], relocated),
            ("2510", reconnect.CLIENT_2510_STAGES["pulse-reconnect"][2], latest),
        ):
            with self.subTest(version=version):
                self.execute_instructions(sites, bindings)

    def execute_instructions(self, sites, targets):
        disassembler = Cs(CS_ARCH_X86, CS_MODE_64)
        disassembler.detail = True
        assembly = [
            ".text",
            ".globl fixture_connect",
            "fixture_connect:",
            ".cfi_startproc",
            ".cfi_escape " + ",".join(hex(b) for b in sites[1][2]),
        ]
        for address, _before, code in sites[:1]:
            for ins in disassembler.disasm(code, address):
                assembly.append(f"at_{ins.address:x}:")
                raw = bytes(ins.bytes)
                target, offset, size = None, 0, 0
                if ins.mnemonic == "call" or ins.group(1):
                    target = int(ins.op_str, 16)
                    offset, size = ins.imm_offset, ins.imm_size
                elif ins.mnemonic == "lea" and any(
                    op.type == X86_OP_MEM and op.mem.base == X86_REG_RIP for op in ins.operands
                ):
                    target = ins.address + ins.size + ins.disp
                    offset, size = ins.disp_offset, ins.disp_size
                if target is None:
                    assembly.append(".byte " + ",".join(hex(b) for b in raw))
                    continue
                label = targets.get(target, f"at_{target:x}")
                assembly += [
                    ".byte " + ",".join(hex(b) for b in raw[:offset]),
                    (".byte " if size == 1 else ".long ") + f"{label} - . - {size}",
                ]
                self.assertEqual(offset + size, len(raw))
        assembly += [".cfi_endproc", '.section .note.GNU-stack,"",@progbits']
        source = r"""
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <unwind.h>
static unsigned char controller[0x30000];
static int depth, started, state, creates, detaches, releases, starts, names;
static void *loop = (void*)0x1000, *old = (void*)0x2000, *fresh = (void*)0x3000;
char client_name[] = "discord_capture", thread_name[] = "pulse_app_captu";
void callback_state(void) {}
void callback_event(void) {}
void fixture_connect(void*);
static int saw_connect;
static _Unwind_Reason_Code unwind_frame(struct _Unwind_Context *context, void *data) {
  uintptr_t ip = _Unwind_GetIP(context);
  if (ip >= (uintptr_t)fixture_connect && ip < (uintptr_t)fixture_connect+193)
    saw_connect=1;
  return _URC_NO_REASON;
}
static void **field(int offset) { return (void**)(controller+offset); }
void loop_lock(void *p) { assert(p==loop); depth++; }
void loop_unlock(void *p) { assert(p==loop && depth>0); depth--; }
void *loop_new(void) { assert(!started && !depth); return loop; }
void *loop_api(void *p) { assert(p==loop && (!started || depth==1)); return loop; }
int ctx_state(void *p) {
  assert(p==old && depth==1);
  saw_connect=0; _Unwind_Backtrace(unwind_frame,0); assert(saw_connect);
  return state;
}
void detach(void *p) {
  assert(p==controller && depth==1);
  loop_lock(loop); detaches++; loop_unlock(loop);
}
void ctx_unref(void *p) { assert(p==old && depth==1); releases++; }
void *ctx_new(void *api, const char *name) {
  assert(api==loop && !strcmp(name,"discord_capture"));
  assert(!started || depth==1); creates++; return fresh;
}
void ctx_set_state(void *p, void *cb, void *user) {
  assert(p==fresh && cb==callback_state && user==controller);
}
void ctx_subscribe(void *p, void *cb, void *user) {
  assert(p==fresh && cb==callback_event && user==controller);
}
int ctx_connect(void *p, void *server, int flags, void *spawn) {
  assert(p==fresh && !server && flags==1 && !spawn); return 0;
}
void loop_name(void *p, const char *name) {
  assert(p==loop && !started && !strcmp(name,"pulse_app_captu")); names++;
}
int loop_start(void *p) { assert(p==loop && !started && !depth); started=1; starts++; return 0; }
int main(void) {
  fixture_connect(controller);
  assert(creates==1 && starts==1 && names==1 && !depth && !detaches && !releases);
  assert(*field(0x270)==loop && *field(0x278)==fresh);
  for (state=0; state<=6; state++) {
    *field(0x278)=old;
    int previous=creates;
    fixture_connect(controller);
    assert(!depth && starts==1 && names==1);
    assert(creates==previous+(state>=5));
    assert(*field(0x278)==(state>=5?fresh:old));
  }
  assert(detaches==2 && releases==2);
}
"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "connect.S").write_text("\n".join(assembly) + "\n")
            (root / "main.c").write_text(source)
            subprocess.run(
                [
                    "cc",
                    "-no-pie",
                    str(root / "connect.S"),
                    str(root / "main.c"),
                    "-o",
                    str(root / "check"),
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run([str(root / "check")], check=True, timeout=5, capture_output=True)
