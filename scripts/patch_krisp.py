"""Run the vendored AUR Krisp patcher, validate the edit and replace atomically."""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from capstone import CS_ARCH_X86, CS_MODE_64, Cs
from capstone.x86 import X86_INS_CALL, X86_INS_JE, X86_INS_NOP, X86_INS_TEST, X86_OP_IMM
from common import ROOT, read_json, sha256, write_json
from elftools.elf.elffile import ELFFile

INITIALIZE = "_ZN7discordL17DoKrispInitializeEv"
SIGNATURE = "_ZN7discord4util17IsSignedByDiscordERKNSt4__Cr12basic_stringIcNS1_11char_traitsIcEENS1_9allocatorIcEEEE"


def patch_site(data: bytes) -> tuple[int, int, bool]:
    elf = ELFFile(io.BytesIO(data))
    if elf.elfclass != 64 or elf.header["e_machine"] != "EM_X86_64":
        raise ValueError("Krisp patcher supports x86_64 ELF only")
    symbols, text = elf.get_section_by_name(".symtab"), elf.get_section_by_name(".text")
    if symbols is None or text is None:
        raise ValueError("Unstripped .symtab and .text required")
    init, check = symbols.get_symbol_by_name(INITIALIZE), symbols.get_symbol_by_name(SIGNATURE)
    if not init or len(init) != 1 or not check or len(check) != 1:
        raise ValueError("Missing or ambiguous Krisp symbols")
    start, destination = init[0].entry.st_value, check[0].entry.st_value
    # The upstream script assumes equal virtual and file addresses. Refuse other
    # layouts rather than silently inheriting its incorrect nonzero conversion.
    if text["sh_addr"] != text["sh_offset"]:
        raise ValueError(
            "Unsupported ELF address/file offset mapping; upstream patcher needs a port"
        )
    if not text["sh_addr"] <= start < text["sh_addr"] + text["sh_size"]:
        raise ValueError("Initializer is outside .text")
    disassembler = Cs(CS_ARCH_X86, CS_MODE_64)
    disassembler.detail = True
    instructions = list(disassembler.disasm(data[start : start + 96], start))
    for index, insn in enumerate(instructions):
        if (
            insn.id == X86_INS_CALL
            and insn.operands[0].type == X86_OP_IMM
            and insn.operands[0].imm == destination
        ):
            tail = instructions[index + 1 : index + 3]
            if len(tail) != 2 or tail[0].id != X86_INS_TEST:
                raise ValueError("Expected signature-check call/test/branch sequence")
            branch = tail[1]
            if branch.id == X86_INS_JE:
                return branch.address, branch.size, False
            if branch.id == X86_INS_NOP:
                count = 0
                for byte in data[branch.address : branch.address + 6]:
                    if byte != 0x90:
                        break
                    count += 1
                if count in (2, 6):
                    return branch.address, count, True
            raise ValueError("Unexpected instruction after Krisp signature test")
    raise ValueError("Krisp signature-check call not found")


def patch(binary: Path, expected: str | None = None) -> dict:
    before_hash = sha256(binary)
    if expected and before_hash != expected:
        raise ValueError("Input binary checksum does not match the locked source")
    original = binary.read_bytes()
    offset, size, already = patch_site(original)
    patcher = ROOT / "packaging/upstream/krisp-patcher.py"
    provenance = read_json(ROOT / "packaging/upstream/provenance.json")
    if sha256(patcher) != provenance["files"]["krisp-patcher.py"]:
        raise ValueError("Vendored patcher differs from pinned upstream")
    with tempfile.TemporaryDirectory(dir=binary.parent) as temporary:
        candidate = Path(temporary) / binary.name
        shutil.copy2(binary, candidate)
        subprocess.run(
            [sys.executable, str(patcher), str(candidate)],
            check=True,
            capture_output=True,
            text=True,
        )
        expected_bytes = original[:offset] + b"\x90" * size + original[offset + size :]
        if candidate.read_bytes() != expected_bytes:
            raise ValueError("Patcher changed bytes outside the expected conditional branch")
        after_hash = sha256(candidate)
        if not already:
            candidate.replace(binary)
    return {
        "input_sha256": before_hash,
        "output_sha256": after_hash,
        "patcher_sha256": sha256(patcher),
        "offset": offset,
        "size": size,
        "already_patched": already,
        "runtime_validated": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = patch(args.binary, args.expected_sha256)
    write_json(args.report, report)
    print(f"Validated Krisp patch at 0x{report['offset']:x}; runtime testing still required")


if __name__ == "__main__":
    main()
