"""Clear Discord's stale PulseAudio mixer samples before attaching a new source.

Only the reviewed x86_64 controller layout is supported. Relocated addresses may
change, but its instructions, buffer layout and lock/unlock targets must match.
See docs/agent/audio-investigation.md for the reproduction and patch rationale.
"""

import hashlib
import io
import shutil
import struct
import tempfile
from pathlib import Path

from capstone import CS_ARCH_X86, CS_MODE_64, Cs, CsError
from capstone.x86 import X86_INS_CALL, X86_INS_JMP, X86_OP_IMM, X86_OP_MEM, X86_REG_RIP
from common import sha256
from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

PREFIX = "_ZN7discord5media10soundshare20PulseAudioController"
ATTACH = PREFIX + "6AttachEjNSt4__Cr12basic_stringIcNS3_11char_traitsIcEENS3_9allocatorIcEEEE"
LAYOUT = {
    PREFIX + "C2Ev": "506fb0eae50a5f8a89bc09c9dcf741de6c51418db696cf714abb20c298257106",
    PREFIX + "4ReadEPhm": "c179fba304fc7dfa99b4a3b29e16355fadb22dd16dd54d02eaadced9f18f52bc",
    PREFIX
    + "12SamplesReadyEP9pa_streamPKsj": "c75cac65000f6b813a7a8eebbdc1c989816930ba6fa8060385e39e3491e8fa92",
    PREFIX
    + "16MonitorSinkInputEPK18pa_sink_input_info": "6f06f417cd297d907653d631dce898310bf5c7db9aef9cef14a8590f4abdfc01",
}

# Exact Attach instructions, with only relative CALL and RIP displacements zeroed.
ATTACH_CODE = bytes.fromhex(
    "5541565385f60f84d300000089f54889fbe80000000039ab800200000f84bd000000"
    "e80000000084c0752b488d3d00000000488d3500000000488d0d000000004c8d0d00000000"
    "ba211000004189e831c0e8000000004c8bb3700200004c89f7e80000000089ab80020000"
    "c6838402000001488bbb78020000e80000000083f8047554c6838402000000e800000000"
    "84c07521488d3d00000000488d3500000000488d0d00000000bae904000031c0e800000000"
    "488bbb78020000488d35000000004889dae8000000004885c074084889c7e800000000"
    "4c89f7e800000000b0015b415e5dc3"
)
LOG_START, LOG_END, STORE_START, RESUME = 0x22, 0x56, 0x65, 0x72
BUFFER_OFFSET, BUFFER_SIZE = 0x286, 192000


def jump(source: int, destination: int) -> bytes:
    return b"\xe9" + struct.pack("<i", destination - source - 5)


def edits() -> tuple[tuple[int, bytes], ...]:
    # Use the optional Attach log's space for a trampoline. The new source's PID
    # store jumps here AFTER mainloop_lock, then resumes before context handling.
    # lea rdi,[rbx+0x286]; xor eax,eax; mov ecx,192000; rep stosb
    reset = bytes.fromhex("488dbb8602000031c0b900ee0200f3aa")
    reset += ATTACH_CODE[STORE_START:RESUME]
    reset += jump(LOG_START + 5 + len(reset), RESUME)
    return (
        (LOG_START, (jump(LOG_START, LOG_END) + reset).ljust(LOG_END - LOG_START, b"\x90")),
        (STORE_START, jump(STORE_START, LOG_START + 5).ljust(RESUME - STORE_START, b"\x90")),
    )


def changed(code: bytes) -> bytes:
    result = bytearray(code)
    for offset, replacement in edits():
        result[offset : offset + len(replacement)] = replacement
    return bytes(result)


def normalized(code: bytes, address: int) -> bytes:
    disassembler = Cs(CS_ARCH_X86, CS_MODE_64)
    disassembler.detail = True
    result = bytearray(code)
    consumed = 0
    for instruction in disassembler.disasm(code, address):
        at = instruction.address - address
        consumed += instruction.size
        if (
            instruction.operands
            and instruction.operands[0].type == X86_OP_IMM
            and (
                instruction.id == X86_INS_CALL
                or (
                    instruction.id == X86_INS_JMP
                    and not address <= instruction.operands[0].imm < address + len(code)
                )
            )
        ):
            start = at + instruction.imm_offset
            result[start : start + instruction.imm_size] = bytes(instruction.imm_size)
        if any(op.type == X86_OP_MEM and op.mem.base == X86_REG_RIP for op in instruction.operands):
            start = at + instruction.disp_offset
            result[start : start + instruction.disp_size] = bytes(instruction.disp_size)
    if consumed != len(code):
        raise ValueError("Incomplete PulseAudio controller disassembly")
    return bytes(result)


def patch_site(data: bytes) -> tuple[int, bool]:
    elf = ELFFile(io.BytesIO(data))
    if elf.elfclass != 64 or not elf.little_endian or elf.header["e_machine"] != "EM_X86_64":
        raise ValueError("PulseAudio buffer patch requires x86_64 ELF")
    symbols, text = elf.get_section_by_name(".symtab"), elf.get_section_by_name(".text")
    if symbols is None or text is None:
        raise ValueError("PulseAudio buffer patch requires unstripped .symtab and .text")

    def function(name):
        found = symbols.get_symbol_by_name(name)
        if not found or len(found) != 1 or found[0]["st_info"]["type"] != "STT_FUNC":
            raise ValueError("Missing or ambiguous PulseAudio controller function: " + name)
        symbol = found[0]
        address, size = symbol["st_value"], symbol["st_size"]
        if (
            not isinstance(symbol["st_shndx"], int)
            or elf.get_section(symbol["st_shndx"]).name != ".text"
            or size <= 0
            or address < text["sh_addr"]
            or address + size > text["sh_addr"] + text["sh_size"]
        ):
            raise ValueError("PulseAudio controller function is outside .text")
        offset = text["sh_offset"] + address - text["sh_addr"]
        return address, offset, data[offset : offset + size]

    for name, expected in LAYOUT.items():
        address, _, code = function(name)
        if hashlib.sha256(normalized(code, address)).hexdigest() != expected:
            raise ValueError("Unknown PulseAudio mixer buffer layout: " + name)
    address, offset, code = function(ATTACH)
    canonical = normalized(code, address)
    already = canonical == changed(ATTACH_CODE)
    if canonical != ATTACH_CODE and not already:
        raise ValueError("Unknown PulseAudio Attach instructions")

    def call_target(relative, name):
        at = address + relative
        destination = at + 5 + struct.unpack_from("<i", code, relative + 1)[0]
        # A direct symbol is used by the executable unit fixture; Discord calls
        # a PLT entry whose first instruction jumps through its named GOT slot.
        direct = symbols.get_symbol_by_name(name) or []
        if any(s["st_shndx"] != "SHN_UNDEF" and s["st_value"] == destination for s in direct):
            return
        for section in elf.iter_sections():
            if section.name not in (".plt", ".plt.sec"):
                continue
            local = destination - section["sh_addr"]
            if not 0 <= local <= section["sh_size"] - 6:
                continue
            entry = section.data()[local : local + 6]
            if entry[:2] != b"\xff\x25":
                continue
            got = destination + 6 + struct.unpack_from("<i", entry, 2)[0]
            for relocations in elf.iter_sections():
                if relocations["sh_type"] not in ("SHT_RELA", "SHT_REL"):
                    continue
                table = elf.get_section(relocations["sh_link"])
                for relocation in relocations.iter_relocations():
                    if (
                        relocation["r_offset"] == got
                        and table.get_symbol(relocation["r_info_sym"]).name == name
                    ):
                        return
        raise ValueError("Unexpected PulseAudio Attach call target: " + name)

    call_target(0x60, "pa_threaded_mainloop_lock")
    call_target(0xDA, "pa_threaded_mainloop_unlock")
    return offset, already


def patch(binary: Path) -> dict:
    original = binary.read_bytes()
    before = hashlib.sha256(original).hexdigest()
    try:
        offset, already = patch_site(original)
    except (ValueError, ELFError, CsError) as error:
        # The updater treats unsupported layouts as a permanent compatibility
        # failure, instead of repeatedly downloading the same unsupported module.
        raise ValueError(f"Unsupported PulseAudio mixer layout: {error}") from error
    candidate = bytearray(original)
    for relative, replacement in edits():
        candidate[offset + relative : offset + relative + len(replacement)] = replacement
    if not already:
        with tempfile.TemporaryDirectory(dir=binary.parent) as temporary:
            target = Path(temporary) / binary.name
            shutil.copy2(binary, target)
            target.write_bytes(candidate)
            if patch_site(target.read_bytes()) != (offset, True):
                raise ValueError("PulseAudio buffer patch failed validation")
            target.replace(binary)
    return {
        "input_sha256": before,
        "output_sha256": sha256(binary),
        "offset": offset,
        "already_patched": already,
        "buffer_bytes": BUFFER_SIZE,
        "patch": "clear-pulse-mixer-before-attach-v1",
    }
