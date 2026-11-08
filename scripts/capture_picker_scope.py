"""Describe the reviewed Linux picker/capture C ABI linkage for external sources."""

import struct
from pathlib import Path

from capture_direct_scope import direct_scope
from common import sha256, write_json
from elftools.elf.elffile import ELFFile
from native_client_2444 import HASHES as CLIENT_2444_HASHES
from native_client_2472 import HASHES as CLIENT_2472_HASHES
from patch_capture_dimensions import OUTPUT_SHA256 as DIMENSIONS_OTHER_SHA256
from patch_capture_dimensions import PINNED_OUTPUT_SHA256 as DIMENSIONS_SHA256
from patch_decoder_eagain import extend_reviewed
from patch_vaapi_hrd import INPUT_SHA256, OUTPUT_SHA256, PINNED_INPUT_SHA256, PINNED_OUTPUT_SHA256

# Full original, HRD-corrected and dimension-corrected revisions. Both revisions'
# four C ABI functions and selected-config get/clear bodies have identical
# normalized instructions; DWARF confirms the same options/result layouts.
REVIEWED = {
    **dict.fromkeys((PINNED_INPUT_SHA256, PINNED_OUTPUT_SHA256, DIMENSIONS_SHA256), 0x22ECB40),
    **dict.fromkeys((INPUT_SHA256, OUTPUT_SHA256, DIMENSIONS_OTHER_SHA256), 0x22F1B40),
}
REVIEWED.update(dict.fromkeys(CLIENT_2444_HASHES, 0x2311B40))
REVIEWED.update(dict.fromkeys(CLIENT_2472_HASHES, 0x231FB90))
extend_reviewed(REVIEWED)


WAKE_OFFSETS = {
    0x231FB90: (
        (15254285, 15164144),
        (15300694, 15164416),
        (15294629, 15164416),
        (14922062, 21941072),
        (14922102, 15087280),
    ),
    0x2311B40: (
        (0xE7FD0D, 0xE69CF0),
        (0xE8B256, 0xE69E00),
        (0xE89AA5, 0xE69E00),
        (0xE2EB4E, 0x14E0550),
        (0xE2EB76, 0xE570B0),
    ),
    0x22F1B40: (
        (0xE799CD, 0xE639B0),
        (0xE84F16, 0xE63AC0),
        (0xE83765, 0xE63AC0),
        (0xE2880E, 0x14DA210),
        (0xE28836, 0xE50D70),
    ),
    0x22ECB40: (
        (0xE8EE2D, 0xE78E10),
        (0xE9A376, 0xE78F20),
        (0xE98BC5, 0xE78F20),
        (0xE5C83E, 0x14E76B0),
        (0xE5C866, 0xE661D0),
    ),
}
WAKE_CONTEXT = (
    ("read", "10next_frame", "4read", 271, "488b7f184883c710", "4889c231c059c3"),
    ("gpu", "20process_dmabuf_frame", "5write", 107, "4889df4889c6", "31ed"),
    ("cpu", "19process_memfd_frame", "5write", 107, "4c89ff4889c6", "488dbc2400010000"),
    (
        "receive",
        "19capture_thread_main",
        "12recv_timeout",
        247,
        "89d14c89e74c89ee4889c2",
        "488b8424080100004883f8ff0f85ef00000080bc2410010000000f854605",
    ),
    ("overdue", "19capture_thread_main", "7overdue", 26, "4889ef", "84c074a0"),
)


def wake_scope(elf, symbols, revision):
    def read(address, size):
        for section in elf.iter_sections():
            offset = address - section["sh_addr"]
            if section["sh_flags"] & 2 and 0 <= offset and offset + size <= section["sh_size"]:
                return section.data()[offset : offset + size]
        raise ValueError("Capture publication address outside reviewed sections")

    relocations = {}
    for section in elf.iter_sections():
        if section["sh_type"] != "SHT_RELA":
            continue
        linked = elf.get_section(section["sh_link"])
        for relocation in section.iter_relocations():
            if relocation["r_info_type"] == 8:
                relocations[relocation["r_offset"]] = relocation["r_addend"]
            elif relocation["r_info_type"] == 6:
                symbol = linked.get_symbol(relocation["r_info_sym"])
                relocations[relocation["r_offset"]] = symbol["st_value"]
    result = {"abi": 1}
    for (call, target), context in zip(WAKE_OFFSETS[revision], WAKE_CONTEXT, strict=True):
        label, host_name, target_name, target_size, before, after = context
        hosts = [
            s
            for s in symbols
            if host_name in s.name and s["st_value"] <= call < s["st_value"] + s["st_size"]
        ]
        targets = [
            s
            for s in symbols
            if target_name in s.name and s["st_value"] == target and s["st_size"] == target_size
        ]
        if len(hosts) != 1 or len(targets) != 1:
            raise ValueError("Capture publication function differs from review")
        before, after = bytes.fromhex(before), bytes.fromhex(after)
        size = 6 if label == "overdue" else 5
        code = read(call, size)
        destination = call + size + struct.unpack_from("<i", code, size - 4)[0]
        if label == "overdue":
            valid_target = code[:2] == b"\xff\x15" and relocations.get(destination) == target
        else:
            valid_target = code[0] == 0xE8 and destination == target
        if (
            not valid_target
            or read(call - len(before), len(before)) != before
            or read(call + size, len(after)) != after
        ):
            raise ValueError("Capture publication call context differs from review")
        result[label] = {"call": call, "target": target}
    return result


def scope(module: Path) -> dict:
    digest = sha256(module)
    if digest not in REVIEWED:
        return {"schema": 1, "supported": False, "reason": "unreviewed-native-module"}
    result = {"schema": 1, "abi": 3, "supported": True, "voice_sha256": digest}
    with module.open("rb") as stream:
        elf = ELFFile(stream)
        table = elf.get_section_by_name(".symtab")
        if elf["e_machine"] != "EM_X86_64" or not table:
            raise ValueError("Reviewed picker linkage requires x86-64 symbols")
        symbols = list(table.iter_symbols())
        result["direct"] = direct_scope(elf, symbols, digest)
        for label, name, size in (
            ("show", "cc_linux_show_picker", 217),
            ("new", "cc_linux_capture_new", 127),
            ("destroy", "cc_linux_capture_destroy", 264),
            ("update", "cc_linux_capture_update", 141),
        ):
            matches = [s for s in symbols if s.name == name]
            if len(matches) != 1 or matches[0]["st_size"] != size:
                raise ValueError("Reviewed picker C ABI symbol changed")
            target = matches[0]
            slots = []
            for section in elf.iter_sections():
                if section["sh_type"] != "SHT_RELA":
                    continue
                linked = elf.get_section(section["sh_link"])
                slots.extend(
                    r["r_offset"]
                    for r in section.iter_relocations()
                    if r["r_info_type"] == 7 and linked.get_symbol(r["r_info_sym"]).name == name
                )
            if len(slots) != 1:
                raise ValueError("Reviewed picker C ABI relocation changed")
            result[label] = {"slot": slots[0], "target": target["st_value"]}
        result["delivery"] = {}
        for label, name, size in (
            ("clone", "cc_video_frame_clone_i420", 548),
            ("release", "cc_i420_buffer_destroy", 46),
            ("destroy", "cc_video_frame_destroy", 81),
        ):
            matches = [s for s in symbols if s.name == name]
            if len(matches) != 1 or matches[0]["st_size"] != size:
                raise ValueError("Reviewed capture delivery C ABI symbol changed")
            result["delivery"][label] = matches[0]["st_value"]
        result["wake"] = wake_scope(elf, symbols, REVIEWED[digest])
        result["selection"] = {}
        base = REVIEWED[digest]
        for label, name, size, address in (
            ("mutex", "_ZN7discord5mediaL26gPickedPipewireConfigMutexE", 40, base),
            ("config", "_ZN7discord5mediaL21gPickedPipewireConfigE.0", 8, base + 0x50),
            ("present", "_ZN7discord5mediaL21gPickedPipewireConfigE.1", 1, base + 0x58),
        ):
            matches = [s for s in symbols if s.name == name]
            if (
                len(matches) != 1
                or matches[0]["st_size"] != size
                or matches[0]["st_value"] != address
            ):
                raise ValueError("Reviewed selected PipeWire configuration changed")
            result["selection"][label] = address
    return result


def write_scope(module: Path, directory: Path) -> dict:
    report = scope(module)
    write_json(directory / "capture-picker-scope.json", report)
    return report
