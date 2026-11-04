"""Describe reviewed PipeWire C ABI linkage used by the Rust capture streams."""

import struct
from pathlib import Path

from common import sha256, write_json
from elftools.elf.elffile import ELFFile
from patch_capture_dimensions import OUTPUT_SHA256 as DIMENSIONS_OUTPUT_SHA256
from patch_capture_dimensions import PINNED_OUTPUT_SHA256 as PINNED_DIMENSIONS_OUTPUT_SHA256
from patch_decoder_eagain import extend_reviewed
from patch_vaapi_hrd import INPUT_SHA256, OUTPUT_SHA256, PINNED_INPUT_SHA256, PINNED_OUTPUT_SHA256

REVIEWED = {
    INPUT_SHA256,
    OUTPUT_SHA256,
    PINNED_INPUT_SHA256,
    PINNED_OUTPUT_SHA256,
    DIMENSIONS_OUTPUT_SHA256,
    PINNED_DIMENSIONS_OUTPUT_SHA256,
}
extend_reviewed(REVIEWED)


def scope(module: Path) -> dict:
    digest = sha256(module)
    if digest not in REVIEWED:
        return {"schema": 1, "supported": False, "reason": "unreviewed-native-module"}
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    with module.open("rb") as stream:
        elf = ELFFile(stream)
        table = elf.get_section_by_name(".symtab")
        if elf["e_machine"] != "EM_X86_64" or not table:
            raise ValueError("Reviewed upstream capture requires x86-64 symbols")
        symbols = list(table.iter_symbols())
        result = {"schema": 1, "supported": True, "voice_sha256": digest}
        for name, suffix in (("connect", "7connect"), ("update", "13update_params")):
            stub_name = "pw_stream_" + ("update_params" if name == "update" else name)
            targets = [s for s in symbols if s.name == stub_name]
            wrappers = [
                s for s in symbols if "_8pipewire6streamNt" in s.name and s.name.endswith(suffix)
            ]
            if len(targets) != 1 or len(wrappers) != 1:
                raise ValueError("Reviewed PipeWire ABI symbols changed")
            target, wrapper = targets[0], wrappers[0]
            slots = []
            for section in elf.iter_sections():
                if section["sh_type"] == "SHT_RELA":
                    slots.extend(
                        r["r_offset"]
                        for r in section.iter_relocations()
                        if r["r_info_type"] == 8 and r["r_addend"] == target["st_value"]
                    )
            if len(slots) != 1:
                raise ValueError("Reviewed PipeWire ABI relocation changed")
            section = elf.get_section(wrapper["st_shndx"])
            offset = wrapper["st_value"] - section["sh_addr"]
            code = section.data()[offset : offset + wrapper["st_size"]]
            calls = []
            for instruction in Cs(CS_ARCH_X86, CS_MODE_64).disasm(code, wrapper["st_value"]):
                raw = bytes(instruction.bytes)
                if raw[:2] == b"\xff\x15" and len(raw) == 6:
                    slot = instruction.address + 6 + struct.unpack_from("<i", raw, 2)[0]
                    if slot == slots[0]:
                        calls.append(instruction.address)
            if len(calls) != 1:
                raise ValueError("Reviewed PipeWire C ABI call changed")
            result[name] = {"slot": slots[0], "target": target["st_value"], "call": calls[0]}
        return result


def write_scope(module: Path, directory: Path) -> dict:
    report = scope(module)
    write_json(directory / "capture-upstream-scope.json", report)
    return report
