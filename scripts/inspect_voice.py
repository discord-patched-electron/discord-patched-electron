"""Inspect locked voice binaries without loading them or starting a client.

Referenced option names and native calls are static evidence, not proof that the
live client supplies correct values or that application audio is isolated.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess

from capstone import Cs, CS_ARCH_X86, CS_MODE_64
from capstone.x86 import X86_OP_MEM, X86_REG_RIP
from elftools.elf.elffile import ELFFile

from common import sha256, write_json

FUNCTIONS = {
    "connection_transport_options": ("_ZN22VoiceConnectionWrapper19SetTransportOptions", ["soundsharePid", "soundshareLoopback", "soundshareDiscardRearChannels"]),
    "desktop_source_options": ("_ZN22VoiceConnectionWrapper27SetDesktopSourceWithOptions", ["sourceId", "type"]),
    "go_live_devices": ("_ZN22VoiceConnectionWrapper16SetGoLiveDevices", ["videoInputDeviceId", "audioInputDeviceId"]),
    "pulse_sink_identification": ("_ZN7discord5media10soundshare20PulseAudioController14SinkIdentified", ["application.process.id"]),
    "pulse_client_identification": ("_ZN7discord5media10soundshare20PulseAudioController13GotClientInfo", ["application.process.id", "pipewire.sec.pid"]),
    "pulse_stream_monitor": ("_ZN7discord5media10soundshare20PulseAudioController16MonitorSinkInput", []),
    "pulse_attach": ("_ZN7discord5media10soundshare20PulseAudioController6Attach", []),
}


def inspect(binary: Path) -> dict:
    functions = {}
    with binary.open("rb") as stream:
        elf = ELFFile(stream)
        if elf.elfclass != 64 or elf.header["e_machine"] != "EM_X86_64":
            raise ValueError("Expected an x86_64 voice module")
        table = elf.get_section_by_name(".symtab")
        if table is None:
            raise ValueError("Native symbols are required for this inspection")
        symbols = list(table.iter_symbols())
        sections = [(s["sh_addr"], s.data()) for s in elf.iter_sections()
                    if s["sh_type"] == "SHT_PROGBITS" and s["sh_flags"] & 2]

        def data_at(address, size):
            for start, data in sections:
                if start <= address < start + len(data):
                    return data[address - start:address - start + size]
            return b""

        disassembler = Cs(CS_ARCH_X86, CS_MODE_64)
        disassembler.detail = True
        for label, (prefix, literals) in FUNCTIONS.items():
            matches = [s for s in symbols if s.name.startswith(prefix) and s["st_info"]["type"] == "STT_FUNC"]
            if len(matches) != 1:
                functions[label] = {"symbol_matches": len(matches)}
                continue
            symbol = matches[0]
            references = []
            for instruction in disassembler.disasm(data_at(symbol["st_value"], symbol["st_size"]), symbol["st_value"]):
                for operand in instruction.operands:
                    if operand.type == X86_OP_MEM and operand.mem.base == X86_REG_RIP:
                        address = instruction.address + instruction.size + operand.mem.disp
                        for literal in literals:
                            if data_at(address, len(literal)) == literal.encode():
                                references.append({"name": literal, "instruction_address": hex(instruction.address)})
            assembly = subprocess.check_output(["objdump", "-d", "--disassemble=" + symbol.name, str(binary)], text=True)
            functions[label] = {"symbol": symbol.name, "address": hex(symbol["st_value"]),
                "size": symbol["st_size"], "referenced_fields": references,
                "pulse_calls": sorted(set(re.findall(r"call\s+[^\n]*?<(pa_[a-z0-9_]+)@plt>", assembly)))}
    return {"schema": 1, "binary_sha256": sha256(binary), "functions": functions,
            "runtime_validated": False, "per_app_audio_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    write_json(args.report, inspect(args.binary))


if __name__ == "__main__":
    main()
