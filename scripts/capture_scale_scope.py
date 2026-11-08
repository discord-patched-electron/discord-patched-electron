"""Bind the GPU capture pixel-limit correction to reviewed native call context."""

import hashlib
import struct
from pathlib import Path

from common import sha256, write_json
from elftools.elf.elffile import ELFFile
from native_client_2444 import HASHES as CLIENT_2444_HASHES
from native_client_2472 import HASHES as CLIENT_2472_HASHES
from patch_capture_dimensions import OUTPUT_SHA256 as DIMENSIONS_OTHER_SHA256
from patch_capture_dimensions import PINNED_OUTPUT_SHA256 as DIMENSIONS_SHA256
from patch_decoder_eagain import extend_reviewed
from patch_vaapi_hrd import INPUT_SHA256, OUTPUT_SHA256, PINNED_INPUT_SHA256, PINNED_OUTPUT_SHA256

REVIEWED = {
    **dict.fromkeys(
        (PINNED_INPUT_SHA256, PINNED_OUTPUT_SHA256, DIMENSIONS_SHA256),
        "24598fba68e3bb3780dbfbeb8ea044920a1ab9a4143b428c7857b3f067313d6c",
    ),
    **dict.fromkeys(
        (INPUT_SHA256, OUTPUT_SHA256, DIMENSIONS_OTHER_SHA256),
        "4783154aa64860d8223c8cb7f7f548e84aa290d96fb85f1bf34e22417d6d8645",
    ),
}
REVIEWED.update(
    dict.fromkeys(
        CLIENT_2444_HASHES, "f11f842e44453c8fa8284272e80429e5465d1f131dac3885424a9dc9aa31ac47"
    )
)
REVIEWED.update(
    dict.fromkeys(
        CLIENT_2472_HASHES, "68efbc1b179baf1402f1785b05e84cfdc700aabf75335fc82b34c8ffdd029078"
    )
)
extend_reviewed(REVIEWED)
CONTEXT = bytes.fromhex(
    "4c8d7c24284c89ff4c89f6e8109dffff8bbba00000008bb3a40000004889f84809f07434"
    "488b542430eb2f8bbba00000008bb3a400000048ba0000900001000000e87a9fffff4989c4"
    "4989d64489642408448974240ceb3231d2"
)


def scope(module: Path) -> dict:
    digest = sha256(module)
    if digest not in REVIEWED:
        return {"schema": 1, "supported": False, "reason": "unreviewed-native-module"}
    with module.open("rb") as stream:
        elf = ELFFile(stream)
        table = elf.get_section_by_name(".symtab")
        if elf["e_machine"] != "EM_X86_64" or not table:
            raise ValueError("Reviewed GPU capture scaling requires x86-64 symbols")
        symbols = {symbol.name: symbol for symbol in table.iter_symbols()}
        function = symbols["_ZN7discord16LinuxVideoSource20UpdateCapturerLockedEv"]
        target = symbols["_ZN7discord19GetScaledResolutionEmmNSt4__Cr8optionalIiEE"]
        section = elf.get_section(function["st_shndx"])
        offset = function["st_value"] - section["sh_addr"]
        code = section.data()[offset : offset + function["st_size"]]
        if hashlib.sha256(code).hexdigest() != REVIEWED[digest] or code[0x50:0xAA] != CONTEXT:
            raise ValueError("GPU capture sink-wants register context differs from review")
        call = function["st_value"] + 0xAA
        if (
            code[0xAA] != 0xE8
            or call + 5 + struct.unpack_from("<i", code, 0xAB)[0] != target["st_value"]
        ):
            raise ValueError("GPU capture scaled-resolution call differs from review")
        return {
            "schema": 1,
            "supported": True,
            "voice_sha256": digest,
            "function_sha256": REVIEWED[digest],
            "call": call,
            "target": target["st_value"],
        }


def write_scope(module: Path, directory: Path) -> dict:
    report = scope(module)
    write_json(directory / "capture-scale-scope.json", report)
    return report
