"""Identify the legacy capability probe without rewriting the proprietary module."""

import hashlib
from pathlib import Path

from capstone import CS_ARCH_X86, CS_MODE_64, Cs
from capture_picker_scope import write_scope as write_capture_picker_scope
from capture_scale_scope import write_scope as write_capture_scale_scope
from capture_scope import write_scope as write_capture_scope
from capture_sync_scope import write_scope as write_capture_sync_scope
from capture_upstream_scope import write_scope as write_capture_upstream_scope
from common import sha256, write_json
from elftools.elf.elffile import ELFFile
from encoder_scope import write_scope as write_encoder_scope
from patch_pulse_audio import normalized

SYMBOL = "_ZN6webrtc18videocapturemodule14DeviceInfoV4l216FillCapabilitiesEi"
FRAME = "_ZN6webrtc18videocapturemodule16VideoCaptureImpl13IncomingFrameEPhmRKNS_22VideoCaptureCapabilityEl"
FRAME_HASH = "9a0b52c0d848dbaf070097f3e9e639d7b8cfb1e740715499c5db3b218dac847e"


def flip_scope(module: Path) -> dict:
    """Unknown camera layouts keep working, but cannot enable outgoing mirroring."""
    with module.open("rb") as stream:
        elf = ELFFile(stream)
        table = elf.get_section_by_name(".symtab")
        frames = table.get_symbol_by_name(FRAME) if table else None
        converters = table.get_symbol_by_name("ConvertToI420") if table else None
        if (
            elf["e_machine"] != "EM_X86_64"
            or not frames
            or len(frames) != 1
            or not converters
            or len(converters) != 1
        ):
            return {"supported": False}
        frame, converter = frames[0], converters[0]
        section = elf.get_section(frame["st_shndx"])
        start = frame["st_value"]
        offset = start - section["sh_addr"]
        code = section.data()[offset : offset + frame["st_size"]]
        if hashlib.sha256(normalized(code, start)).hexdigest() != FRAME_HASH:
            return {"supported": False}
        calls = [
            instruction.address
            for instruction in Cs(CS_ARCH_X86, CS_MODE_64).disasm(code, start)
            if instruction.mnemonic == "call" and instruction.op_str == hex(converter["st_value"])
        ]
        if len(calls) != 1:
            return {"supported": False}
        return {
            "supported": True,
            "voice_sha256": sha256(module),
            "call": calls[0],
            "converter": converter["st_value"],
        }


def scope(module: Path) -> dict:
    with module.open("rb") as stream:
        elf = ELFFile(stream)
        table = elf.get_section_by_name(".symtab")
        symbols = table.get_symbol_by_name(SYMBOL) if table else None
        if elf["e_machine"] != "EM_X86_64" or not symbols or len(symbols) != 1:
            raise ValueError("Unknown camera capability probe; review the new voice module")
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
    (directory / "camera-probe.conf").write_text(f"{report['start']:#x} {report['end']:#x}\n")
    write_json(directory / "camera-flip.json", flip_scope(module))
    write_capture_scope(module, directory)
    write_capture_scale_scope(module, directory)
    write_capture_upstream_scope(module, directory)
    write_capture_sync_scope(module, directory)
    write_capture_picker_scope(module, directory)
    write_encoder_scope(module, directory)
    return report
