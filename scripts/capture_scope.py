"""Describe reviewed Linux capture call sites; unknown modules keep native behavior."""

import hashlib
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
    with module.open("rb") as stream:
        elf = ELFFile(stream)
        table = elf.get_section_by_name(".symtab")
        if elf["e_machine"] != "EM_X86_64" or not table:
            return {"schema": 1, "supported": False, "reason": "unsupported-architecture"}
        symbols = list(table.iter_symbols())
        process = next(s for s in symbols if s.name.endswith("23process_pipewire_stream"))
        dequeue = next(
            s
            for s in symbols
            if "_8pipewire6stream" in s.name and s.name.endswith("14dequeue_buffer")
        )
        drop = next(
            s for s in symbols if "_8pipewire6buffer" in s.name and s.name.endswith("4Drop4drop")
        )
        relocation_targets = {}
        for section in elf.iter_sections():
            if section["sh_type"] != "SHT_RELA":
                continue
            dynamic_symbols = elf.get_section(section["sh_link"])
            for relocation in section.iter_relocations():
                symbol = dynamic_symbols.get_symbol(relocation["r_info_sym"])
                target = symbol["st_value"] if relocation["r_info_sym"] else relocation["r_addend"]
                relocation_targets[relocation["r_offset"]] = target
        section = elf.get_section(process["st_shndx"])
        start = process["st_value"]
        offset = start - section["sh_addr"]
        code = section.data()[offset : offset + process["st_size"]]
        # The two reviewed builds use the same callback and Rust return layout.
        # Locate exact indirect calls by their resolved relocation, not byte
        # offsets transplanted from another module version.
        from capstone import CS_ARCH_X86, CS_MODE_64, Cs

        sites = {}
        for instruction in Cs(CS_ARCH_X86, CS_MODE_64).disasm(code, start):
            raw = bytes(instruction.bytes)
            if instruction.mnemonic != "call" or len(raw) != 6 or raw[:2] != b"\xff\x15":
                continue
            slot = instruction.address + 6 + struct.unpack("<i", raw[2:])[0]
            target = relocation_targets.get(slot)
            for name, function in (("dequeue", dequeue), ("release", drop)):
                if target == function["st_value"]:
                    if name in sites:
                        raise ValueError("Capture callback has an ambiguous call site")
                    sites[name] = {
                        "call": instruction.address,
                        "target": target,
                        "bytes": raw.hex(),
                    }
        if set(sites) != {"dequeue", "release"}:
            raise ValueError("Reviewed capture callback no longer has the expected calls")
        return {
            "schema": 1,
            "supported": True,
            "voice_sha256": digest,
            "callback_sha256": hashlib.sha256(code).hexdigest(),
            "callback": start,
            "callback_size": process["st_size"],
            **sites,
        }


def write_scope(module: Path, directory: Path) -> dict:
    report = scope(module)
    write_json(directory / "capture-scope.json", report)
    return report
