"""Scope the direct capture adapter to reviewed C exports, never Rust layouts."""

import hashlib

EXPORTS = (
    ("cc_video_frame_destroy", 81),
    ("cc_video_frame_get_data", 22),
    ("cc_video_frame_clone_i420", 548),
    ("cc_i420_buffer_destroy", 46),
    ("cc_video_frame_get_nv12_data", 142),
    ("cc_video_frame_get_abgr_data", 83),
    ("cc_video_frame_has_cpu_frame", 3),
    ("cc_video_frame_has_gpu_frame", 20),
    ("cc_video_frame_get_rendition", 3),
    ("cc_linux_capture_set_requested_renditions", 344),
)
# Physically reviewed prepared module. Other picker scopes retain their portal
# path until their frame/capture/encoder C boundaries have been reviewed too.
REVIEWED = {"7891b5ef54457b80df036a8b38d59045e9dbcca881d8c8b66f29e88fd050f479"}


def preview_scope(elf):
    """Review the C++ rendering boundary separately from the capture/encoder ABI."""
    import struct

    table = elf.get_section_by_name(".symtab")

    def symbol(name, size):
        found = table.get_symbol_by_name(name)
        if not found or len(found) != 1 or found[0]["st_size"] != size:
            raise ValueError("Direct preview native boundary differs from review")
        return found[0]

    def data(item):
        section = elf.get_section(item["st_shndx"])
        offset = item["st_value"] - section["sh_addr"]
        return section.data()[offset : offset + item["st_size"]]

    on_frame = symbol("_ZN7discord5media18DirectOutputStream7OnFrameERKN6webrtc10VideoFrameE", 149)
    vtable = symbol("_ZTVN7discord5media18DirectOutputStreamE", 56)
    buffer_vtable = symbol("_ZTVN6webrtc16RefCountedObjectIN7discord17WumpusFrameBufferEEE", 136)
    handle = symbol("_ZNK7discord17WumpusFrameBuffer6HandleEv", 5)
    getter = symbol("_ZNK6webrtc10VideoFrame18video_frame_bufferEv", 36)
    slot = vtable["st_value"] + 32
    if struct.unpack_from("<Q", data(vtable), 32)[0] != on_frame["st_value"]:
        raise ValueError("Direct preview vtable differs from review")
    relocations = [
        r
        for section in elf.iter_sections()
        if section["sh_type"] == "SHT_RELA"
        for r in section.iter_relocations()
        if r["r_offset"] == slot
    ]
    if (
        len(relocations) != 1
        or relocations[0]["r_info_type"] != 8
        or relocations[0]["r_addend"] != on_frame["st_value"]
        or data(handle) != bytes.fromhex("488b4748c3")
        or bytes.fromhex("488b7e08") not in data(getter)
    ):
        raise ValueError("Direct preview native accessor differs from review")
    return {
        "abi": 1,
        "slot": slot,
        "target": on_frame["st_value"],
        "bufferVtable": buffer_vtable["st_value"] + 16,
        "handle": handle["st_value"],
        "getter": getter["st_value"],
        "bodySha256": hashlib.sha256(data(on_frame)).hexdigest(),
    }


def direct_scope(elf, symbols, digest):
    if digest not in REVIEWED:
        return {"abi": 1, "supported": False}
    result = {"abi": 1, "supported": True, "exports": []}
    result["preview"] = preview_scope(elf)
    for name, size in EXPORTS:
        matches = [s for s in symbols if s.name == name]
        if len(matches) != 1 or matches[0]["st_size"] != size:
            raise ValueError("Direct capture C export differs from review")
        symbol = matches[0]
        slots = []
        for section in elf.iter_sections():
            if section["sh_type"] != "SHT_RELA":
                continue
            table = elf.get_section(section["sh_link"])
            slots.extend(
                r["r_offset"]
                for r in section.iter_relocations()
                if r["r_info_type"] == 7 and table.get_symbol(r["r_info_sym"]).name == name
            )
        if len(slots) != 1:
            raise ValueError("Direct capture C relocation differs from review")
        section = elf.get_section(symbol["st_shndx"])
        start = symbol["st_value"] - section["sh_addr"]
        code = section.data()[start : start + size]
        result["exports"].append(
            {
                "name": name,
                "slot": slots[0],
                "target": symbol["st_value"],
                "size": size,
                "sha256": hashlib.sha256(code).hexdigest(),
            }
        )
    return result
