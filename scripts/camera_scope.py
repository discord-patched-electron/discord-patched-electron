"""Identify the legacy capability probe without rewriting the proprietary module."""

from pathlib import Path

from elftools.elf.elffile import ELFFile

from common import sha256, write_json

SYMBOL = "_ZN6webrtc18videocapturemodule14DeviceInfoV4l216FillCapabilitiesEi"


def scope(module: Path) -> dict:
    with module.open("rb") as stream:
        elf = ELFFile(stream)
        table = elf.get_section_by_name(".symtab")
        symbols = table.get_symbol_by_name(SYMBOL) if table else None
        if elf["e_machine"] != "EM_X86_64" or not symbols or len(symbols) != 1:
            raise ValueError(
                "Unknown camera capability probe; review the new voice module"
            )
        symbol = symbols[0]
        section = elf.get_section(symbol["st_shndx"])
        start, size = symbol["st_value"], symbol["st_size"]
        if (
            symbol["st_info"]["type"] != "STT_FUNC"
            or not 0 < size < 65536
            or not section["sh_flags"] & 4
            or start < section["sh_addr"]
            or start + size > section["sh_addr"] + section["sh_size"]
        ):
            raise ValueError("Invalid camera capability function range")
    return {
        "voice_sha256": sha256(module),
        "symbol": SYMBOL,
        "start": start,
        "end": start + size,
    }


def write_scope(module: Path, directory: Path) -> dict:
    report = scope(module)
    write_json(directory / "camera-probe.json", report)
    (directory / "camera-probe.conf").write_text(
        f"{report['start']:#x} {report['end']:#x}\n"
    )
    return report
