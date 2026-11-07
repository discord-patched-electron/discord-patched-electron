"""Review the native encoder C ABI and its three writable linkage slots."""

import hashlib
import struct
from pathlib import Path

from common import sha256, write_json
from elftools.elf.elffile import ELFFile
from patch_capture_dimensions import OUTPUT_SHA256 as DIMENSIONS_OUTPUT_SHA256
from patch_capture_dimensions import PINNED_OUTPUT_SHA256 as PINNED_DIMENSIONS_OUTPUT_SHA256
from patch_decoder_eagain import extend_reviewed
from patch_vaapi_hrd import INPUT_SHA256, OUTPUT_SHA256, PINNED_INPUT_SHA256, PINNED_OUTPUT_SHA256

# Separate from capture's review set: adding a new capture implementation does
# not establish its encoder ABI. Both versions' C++ DWARF and call sites were
# independently inspected; the HRD/pool patches leave this C ABI unchanged.
REVIEWED = {
    INPUT_SHA256,
    OUTPUT_SHA256,
    PINNED_INPUT_SHA256,
    PINNED_OUTPUT_SHA256,
    DIMENSIONS_OUTPUT_SHA256,
    PINNED_DIMENSIONS_OUTPUT_SHA256,
}
extend_reviewed(REVIEWED)


def sequence_scope(elf) -> dict:
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    table = elf.get_section_by_name(".symtab")
    symbols = list(table.iter_symbols())
    targets = {s["st_value"] for s in symbols if "22create_sequence_header" in s.name}
    if len(targets) != 1:
        raise ValueError("Reviewed AV1 sequence constructor changed")
    target = targets.pop()
    calls = []
    for symbol in symbols:
        if "16new_native_vaapi" not in symbol.name or "3av13AV1" not in symbol.name:
            continue
        section = elf.get_section(symbol["st_shndx"])
        start = symbol["st_value"]
        offset = start - section["sh_addr"]
        code = section.data()[offset : offset + symbol["st_size"]]
        for instruction in Cs(CS_ARCH_X86, CS_MODE_64).disasm(code, start):
            raw = bytes(instruction.bytes)
            if len(raw) == 5 and raw[0] == 0xE8:
                if instruction.address + 5 + struct.unpack_from("<i", raw, 1)[0] == target:
                    calls.append(instruction.address)
    if len(set(calls)) != 2:
        raise ValueError("Reviewed AV1 constructor call sites changed")
    return {"target": target, "calls": sorted(set(calls))}


def quality_scope(elf) -> dict:
    """Bind the reviewed Rust tuning return layout and VA display access."""
    table = elf.get_section_by_name(".dynsym")
    targets = [s for s in table.iter_symbols() if s.name.endswith("14create_tunings")]
    queries = table.get_symbol_by_name("vaQueryVendorString")
    if len(targets) != 1 or not queries or len(queries) != 1:
        raise ValueError("Reviewed VAAPI tuning exports changed")
    target = targets[0]
    slots = []
    for section in elf.iter_sections():
        if section["sh_type"] != "SHT_RELA":
            continue
        symbols = elf.get_section(section["sh_link"])
        slots.extend(
            relocation["r_offset"]
            for relocation in section.iter_relocations()
            if relocation["r_info_type"] == 6  # R_X86_64_GLOB_DAT, eagerly resolved
            and symbols.get_symbol(relocation["r_info_sym"]).name == target.name
        )
    if len(slots) != 1:
        raise ValueError("Reviewed VAAPI tuning linkage changed")
    return {"slot": slots[0], "target": target["st_value"], "query": queries[0]["st_value"]}


def qp_trust_scope(elf) -> dict:
    """Bind the reviewed RustEncoder sret method and its exact relocated vtable slot."""
    table = elf.get_section_by_name(".symtab")
    functions = table.get_symbol_by_name("_ZNK7discord11RustEncoder14GetEncoderInfoEv")
    vtables = table.get_symbol_by_name("_ZTVN7discord11RustEncoderE")
    if not functions or len(functions) != 1 or not vtables or len(vtables) != 1:
        raise ValueError("Reviewed encoder QP capability symbols changed")
    target = functions[0]["st_value"]
    vtable = vtables[0]
    slot = vtable["st_value"] + 0x40
    section = elf.get_section(vtable["st_shndx"])
    if (
        vtable["st_size"] < 0x48
        or struct.unpack_from("<Q", section.data(), slot - section["sh_addr"])[0] != target
    ):
        raise ValueError("Reviewed encoder QP capability vtable changed")
    relocations = [
        relocation
        for section in elf.iter_sections()
        if section["sh_type"] == "SHT_RELA"
        for relocation in section.iter_relocations()
        if relocation["r_offset"] == slot
    ]
    if (
        len(relocations) != 1
        or relocations[0]["r_info_type"] != 8  # R_X86_64_RELATIVE
        or relocations[0]["r_addend"] != target
    ):
        raise ValueError("Reviewed encoder QP capability relocation changed")
    accessors = table.get_symbol_by_name("_ZNK6webrtc15EncoderSettings14encoder_configEv")
    callers = table.get_symbol_by_name(
        "_ZN6webrtc33VideoStreamEncoderResourceManager31ConfigureBandwidthQualityScaler"
        "ERKNS_12VideoEncoder11EncoderInfoE"
    )
    if not accessors or len(accessors) != 1 or not callers or len(callers) != 1:
        raise ValueError("Reviewed bandwidth scaler accessor changed")
    caller = callers[0]
    section = elf.get_section(caller["st_shndx"])
    offset = caller["st_value"] - section["sh_addr"]
    code = section.data()[offset : offset + caller["st_size"]]
    # RSI stays the original EncoderInfo argument up to this one accessor call.
    # Native degradation/has-settings checks must remain before the call.
    prefix = bytes.fromhex(
        "554889e54157415653504989f64889fb8b477883e0fd83f801753380bba809000001752a488dbb68020000"
    )
    # Only the returned config's one-byte gate is read, then RAX is discarded.
    suffix = bytes.fromhex("80b8c9000000017515450fb6bef00000004180f7014522bef1000000eb03")
    call = caller["st_value"] + len(prefix)
    if (
        not code.startswith(prefix + b"\xe8")
        or code[len(prefix) + 5 : len(prefix) + 5 + len(suffix)] != suffix
        or call + 5 + struct.unpack_from("<i", code, len(prefix) + 1)[0] != accessors[0]["st_value"]
    ):
        raise ValueError("Reviewed bandwidth gate register/data flow changed")
    return {
        "slot": slot,
        "target": target,
        "bandwidth_call": call,
        "config_target": accessors[0]["st_value"],
    }


def transition_scope(elf) -> dict:
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    table = elf.get_section_by_name(".symtab")
    names = {
        "quality": "_ZN6webrtc33VideoStreamEncoderResourceManager22ConfigureQualityScalerERKNS_12VideoEncoder11EncoderInfoE",
        "bandwidth": "_ZN6webrtc33VideoStreamEncoderResourceManager31ConfigureBandwidthQualityScalerERKNS_12VideoEncoder11EncoderInfoE",
        "started": "_ZNK6webrtc30BandwidthQualityScalerResource10is_startedEv",
        "codec": "_ZN6webrtc26GetVideoCodecTypeOrGenericERKNSt4__Cr8optionalINS_15EncoderSettingsEEE",
    }
    selected = {}
    for key, name in names.items():
        matches = table.get_symbol_by_name(name)
        if not matches or len(matches) != 1:
            raise ValueError("Reviewed scaler transition symbol changed: " + key)
        selected[key] = matches[0]

    def code(symbol):
        section = elf.get_section(symbol["st_shndx"])
        offset = symbol["st_value"] - section["sh_addr"]
        return section.data()[offset : offset + symbol["st_size"]]

    if code(selected["started"]) != bytes.fromhex("554889e548837f60000f95c05dc3") or code(
        selected["codec"]
    ) != bytes.fromhex("554889e531c080bf400700000074068b87100100005dc3"):
        raise ValueError("Reviewed scaler owner-state accessors changed")
    bandwidth = code(selected["bandwidth"])
    # Owner resource is manager+0x48, passed directly to the reviewed accessor.
    relative = bandwidth.find(bytes.fromhex("488b7b48e8"))
    if (
        relative < 0
        or selected["bandwidth"]["st_value"]
        + relative
        + 9
        + struct.unpack_from("<i", bandwidth, relative + 5)[0]
        != selected["started"]["st_value"]
    ):
        raise ValueError("Reviewed bandwidth resource ownership changed")
    calls = {"quality": set(), "bandwidth": set()}
    for symbol in table.iter_symbols():
        if "VideoStreamEncoder" not in symbol.name or not symbol["st_size"]:
            continue
        if symbol["st_info"]["type"] != "STT_FUNC":
            continue
        for instruction in Cs(CS_ARCH_X86, CS_MODE_64).disasm(code(symbol), symbol["st_value"]):
            raw = bytes(instruction.bytes)
            if len(raw) != 5 or raw[0] != 0xE8:
                continue
            target = instruction.address + 5 + struct.unpack_from("<i", raw, 1)[0]
            for key in calls:
                if target == selected[key]["st_value"]:
                    calls[key].add(instruction.address)
    if any(len(sites) != 3 for sites in calls.values()):
        raise ValueError("Reviewed scaler transition call sites changed")
    return {
        **{
            key: {"target": selected[key]["st_value"], "calls": sorted(calls[key])} for key in calls
        },
        "started": selected["started"]["st_value"],
        "codec": selected["codec"]["st_value"],
    }


def allocation_scope(elf) -> dict:
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    table = elf.get_section_by_name(".symtab")

    def symbol(name):
        matches = table.get_symbol_by_name(name)
        if not matches or len(matches) != 1:
            raise ValueError("Reviewed allocation symbol changed: " + name)
        return matches[0]

    def code(entry):
        section = elf.get_section(entry["st_shndx"])
        offset = entry["st_value"] - section["sh_addr"]
        return section.data()[offset : offset + entry["st_size"]]

    rate = symbol("_ZNK6webrtc14RateStatistics4RateEl")
    budget = symbol("_ZN6webrtc33VideoStreamEncoderResourceManager16SetTargetBitrateENS_8DataRateE")
    if not code(budget).startswith(
        bytes.fromhex(
            "554889e54156534889f34989fe4885f674394180bef000000000750841c686f00000000141899eec000000"
        )
    ):
        raise ValueError("Reviewed native allocation storage changed")
    query_calls, budget_calls = [], []
    for entry in table.iter_symbols():
        if entry["st_info"]["type"] != "STT_FUNC" or not entry["st_size"]:
            continue
        is_query = (
            "RemoteInvoker" in entry.name
            and "BandwidthQualityScaler20StartCheckForBitrate" in entry.name
        )
        is_budget = "VideoStreamEncoder16OnBitrateUpdated" in entry.name
        if not is_query and not is_budget:
            continue
        body = code(entry)
        for instruction in Cs(CS_ARCH_X86, CS_MODE_64).disasm(body, entry["st_value"]):
            raw = bytes(instruction.bytes)
            if len(raw) != 5 or raw[0] != 0xE8:
                continue
            target = instruction.address + 5 + struct.unpack_from("<i", raw, 1)[0]
            if is_query and target == rate["st_value"]:
                offset = instruction.address - entry["st_value"]
                if body[offset - 8 : offset] != bytes.fromhex("488d7b20488b7310") or body[
                    offset + 5 : offset + 8
                ] != bytes.fromhex("80fa01"):
                    raise ValueError("Reviewed allocation query ABI changed")
                query_calls.append(instruction.address)
            if is_budget and target == budget["st_value"]:
                budget_calls.append(instruction.address)
    if len(query_calls) != 1 or len(budget_calls) != 1:
        raise ValueError("Reviewed allocation query call sites changed")
    vtable = symbol("_ZTVN6webrtc22BandwidthQualityScalerE")
    destructors = []
    for index, suffix in enumerate(("D1Ev", "D0Ev")):
        target = symbol("_ZN6webrtc22BandwidthQualityScaler" + suffix)["st_value"]
        slot = vtable["st_value"] + 0x10 + 8 * index
        if struct.unpack_from("<Q", code(vtable), 0x10 + 8 * index)[0] != target:
            raise ValueError("Reviewed allocation lifetime vtable changed")
        matches = [
            r
            for section in elf.iter_sections()
            if section["sh_type"] == "SHT_RELA"
            for r in section.iter_relocations()
            if r["r_offset"] == slot
        ]
        if len(matches) != 1 or matches[0]["r_info_type"] != 8 or matches[0]["r_addend"] != target:
            raise ValueError("Reviewed allocation lifetime relocation changed")
        destructors.append({"slot": slot, "target": target})
    return {
        "rate_call": query_calls[0],
        "rate_target": rate["st_value"],
        "budget_call": budget_calls[0],
        "budget_target": budget["st_value"],
        "destructors": destructors,
    }


def stream_limit_scope(elf) -> dict:
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    table = elf.get_section_by_name(".symtab")

    def symbol(name):
        matches = table.get_symbol_by_name(name)
        if not matches or len(matches) != 1:
            raise ValueError("Reviewed stream envelope symbol changed: " + name)
        return matches[0]

    def code(entry):
        section = elf.get_section(entry["st_shndx"])
        offset = entry["st_value"] - section["sh_addr"]
        return section.data()[offset : offset + entry["st_size"]]

    caller = symbol("_ZN6webrtc18VideoStreamEncoder18ReconfigureEncoderEv")
    target = symbol(
        "_ZN6webrtc19EncoderInfoSettings55GetSinglecastBitrateLimitForResolutionWhenQpIsUntrustedENSt4__Cr8optionalIiEERKNS1_6vectorINS_12VideoEncoder23ResolutionBitrateLimitsENS1_9allocatorIS6_EEEE"
    )
    destructor = symbol("_ZN6webrtc12VideoEncoder11EncoderInfoD1Ev")
    body = code(caller)
    if not body.startswith(bytes.fromhex("554889e54157415641554154534881ecf81a00004989fc")):
        raise ValueError("Reviewed stream envelope owner register changed")
    # Owner encoder pointer, virtual sret GetEncoderInfo slot and advertised
    # limits vector offset. Content type+0x1a0 and codec+0x118 are native fields.
    for sequence in (
        "498bb424e8010000488b06488dbd70eaffffff5060",
        "488b8520ebffff483b8528ebffff",
        "4183bc24a001000001",
        "4d8dbc2418010000418b37",
    ):
        if bytes.fromhex(sequence) not in body:
            raise ValueError("Reviewed stream envelope owner/metadata layout changed")
    calls = []
    for instruction in Cs(CS_ARCH_X86, CS_MODE_64).disasm(body, caller["st_value"]):
        raw = bytes(instruction.bytes)
        if len(raw) != 5 or raw[0] != 0xE8:
            continue
        if instruction.address + 5 + struct.unpack_from("<i", raw, 1)[0] != target["st_value"]:
            continue
        offset = instruction.address - caller["st_value"]
        if body[offset - 14 : offset] != bytes.fromhex("488dbd70eaffff488d95f0e7ffff") or body[
            offset + 5 : offset + 12
        ] != bytes.fromhex("80bd80eaffff01"):
            raise ValueError("Reviewed stream envelope result/call ABI changed")
        calls.append(instruction.address)
    if len(calls) != 1:
        raise ValueError("Reviewed stream envelope call count changed")
    return {
        "call": calls[0],
        "target": target["st_value"],
        "info_destructor": destructor["st_value"],
    }


def hevc_scope(elf) -> dict:
    """Bind both frame C ABIs and the borrowed NV12/Arc lifetime accessors."""
    symbols = elf.get_section_by_name(".dynsym")

    def unique(suffix):
        matches = [s for s in symbols.iter_symbols() if s.name.endswith(suffix)]
        if len(matches) != 1 or not matches[0]["st_value"]:
            raise ValueError("Reviewed HEVC accessor changed: " + suffix)
        return matches[0]

    linkage = []
    for name in (
        "cc_encoder_add_frame_native",
        "cc_encoder_add_frame_i420",
        "cc_encoder_supports_recon_frame",
        "cc_encoder_vendor",
        "cc_probe_gpu_encoder",
    ):
        function = unique(name)
        slots = []
        for section in elf.iter_sections():
            if section["sh_type"] != "SHT_RELA":
                continue
            table = elf.get_section(section["sh_link"])
            slots.extend(
                r["r_offset"]
                for r in section.iter_relocations()
                if r["r_info_type"] in (6, 7) and table.get_symbol(r["r_info_sym"]).name == name
            )
        if len(slots) != 1:
            raise ValueError("Reviewed HEVC linkage changed: " + name)
        plt = elf.get_section_by_name(".plt")
        code = plt.data()
        lazy = [
            plt["sh_addr"] + i + 6
            for i in range(0, len(code) - 15, 16)
            if code[i : i + 2] == b"\xff\x25"
            and plt["sh_addr"] + i + 6 + struct.unpack_from("<i", code, i + 2)[0] == slots[0]
        ]
        if len(lazy) > 1:
            raise ValueError("Ambiguous HEVC lazy resolver: " + name)
        linkage.extend((slots[0], function["st_value"], lazy[0] if lazy else 0))
    descriptor = unique("19PooledVulkanSurface10descriptor")
    section = elf.get_section(descriptor["st_shndx"])
    at = descriptor["st_value"] - section["sh_addr"]
    if section.data()[at : at + 17] != bytes.fromhex("83bfb00000000274084883c7084889f8c3"):
        raise ValueError("Reviewed NV12 descriptor ownership layout changed")
    drop = unique("13capture_linux10LinuxFrameE9drop_slowBI_")
    linkage.extend(
        (
            descriptor["st_value"],
            drop["st_value"],
            unique("cc_video_frame_clone_i420")["st_value"],
            unique("cc_i420_buffer_destroy")["st_value"],
        )
    )
    return {"abi": 1, "offsets": linkage}


def scope(module: Path) -> dict:
    checksum = sha256(module)
    if checksum not in REVIEWED:
        return {"schema": 1, "supported": False, "reason": "unreviewed-native-module"}
    with module.open("rb") as stream:
        elf = ELFFile(stream)
        if elf["e_machine"] != "EM_X86_64":
            raise ValueError("Reviewed encoder ABI requires x86-64")
        symbols = elf.get_section_by_name(".dynsym")
        result = {"schema": 1, "supported": True, "voice_sha256": checksum, "abi": 1}
        for name in ("create", "update", "destroy", "vendor"):
            symbol_name = "cc_encoder_" + name
            functions = symbols.get_symbol_by_name(symbol_name)
            if not functions or len(functions) != 1 or not functions[0]["st_value"]:
                raise ValueError("Reviewed encoder symbol changed: " + name)
            entry = {"target": functions[0]["st_value"]}
            if name != "vendor":
                slots = []
                for section in elf.iter_sections():
                    if section["sh_type"] != "SHT_RELA":
                        continue
                    table = elf.get_section(section["sh_link"])
                    slots.extend(
                        relocation["r_offset"]
                        for relocation in section.iter_relocations()
                        if relocation["r_info_type"] == 7  # R_X86_64_JUMP_SLOT
                        and table.get_symbol(relocation["r_info_sym"]).name == symbol_name
                    )
                if len(slots) != 1:
                    raise ValueError("Reviewed encoder linkage changed: " + name)
                entry["slot"] = slots[0]
                plt = elf.get_section_by_name(".plt")
                code = plt.data() if plt else b""
                lazy = []
                for offset in range(0, len(code) - 15, 16):
                    address = plt["sh_addr"] + offset
                    stub = code[offset : offset + 16]
                    if (
                        stub[:2] == b"\xff\x25"
                        and stub[6] == 0x68
                        and stub[11] == 0xE9
                        and address + 6 + struct.unpack_from("<i", stub, 2)[0] == slots[0]
                        and address + 16 + struct.unpack_from("<i", stub, 12)[0] == plt["sh_addr"]
                    ):
                        lazy.append(address + 6)
                if len(lazy) != 1:
                    raise ValueError("Reviewed encoder lazy linkage changed: " + name)
                entry["lazy"] = lazy[0]
            result[name] = entry
        result["av1_sequence"] = sequence_scope(elf)
        result["amd_quality"] = quality_scope(elf)
        result["qp_trust"] = qp_trust_scope(elf)
        result["transitions"] = transition_scope(elf)
        result["allocation"] = allocation_scope(elf)
        result["stream_limit"] = stream_limit_scope(elf)
        result["hevc"] = hevc_scope(elf)
        result["decoder_hardware"] = decoder_scope(elf)
        result["software_av1_crop"] = software_av1_scope(elf)
        return result


def software_av1_scope(elf):
    """Review both native dav1d wrappers and their public input/output C ABI.

    Configure requests max_frame_delay=1. Decode leaves dav1d timestamps unused
    and builds RTP/NTP timing separately from EncodedImage. Picture dimensions
    and planar data use the public dav1d prefix; unref releases its stored ref,
    whose allocator retains the original allocation, not the caller's view.
    """
    table = elf.get_section_by_name(".symtab")
    prefix = "_ZN6webrtc12_GLOBAL__N_112Dav1dDecoder"
    suffixes = (
        "9ConfigureERKNS_12VideoDecoder8SettingsE",
        "6DecodeERKNS_12EncodedImageEl",
        "D2Ev",
        "D0Ev",
        "7ReleaseEv",
    )
    reviewed = (
        (
            "3f51ef490ed01ed1ccbb1a0457ebf13f19926b4679d1f742a8d099aafde2d3cb",
            "50bdad263a44d4a76602d23696c74b86c97cf691122185a03ce9847d73728d53",
            "1fe58489e3840bb4c9d92f490e978be09b6849bf7f7b21f9b017ef8d0a3427ec",
            "6866fac36662e89a4cc0067bfd314d02f0ed359f995c1eea65f05178335dfc1d",
            "1c7d2a2530aa685bdcbac7c9998e11450dc765f973781df78b1ea066ea74e0b3",
        ),
        (
            "042674ece55b8bf08b5fcca00a41eb5c9465297407074869e8edb0ae40de9423",
            "3cb510cc68e3b5daec3d88f6fd301df02b1d3e9234c8eceb768b6db811d26e94",
            "6879e91c5020803d9ba0ff029ad1967b8ed408d538044a4a7f1d57d916716e0a",
            "98ae6a14b3e09634ed190450634604f3f7a3070f630f223ca704ed6d53cf7da4",
            "59a745f32cc580f7be5baf00d54009d9f27b0000986604d3d635ba812e0af498",
        ),
        (
            "5045d62bb574cbd5a799a2e286d3003f5dce588386dafac56919e299479a3d94",
            "8c9eeac6cfd72ead23f73b178843449a184690643e77e2e9aef756a9edf91d18",
            "8560308e230a8b9afe61a1676cc0f113a22bc661e717b9894a0f1d2f85b4cf8f",
            "5df060f2332ce5f0ea874856878f6b565a21d7bd5fcee7506e0db94a8236fe65",
            "b3fb1c44a3645a0a12be51e3d0b12174e28a73e8cc4096596ba697fd4d346db7",
        ),
    )
    symbols, codes = [], []
    for suffix in suffixes:
        matches = table.get_symbol_by_name(prefix + suffix) or []
        if not matches and not symbols:
            return None  # Non-dav1d test fixtures/implementations have no hook.
        if len(matches) != 1:
            raise ValueError("Software AV1 decoder symbols differ from review")
        symbol = matches[0]
        section = elf.get_section(symbol["st_shndx"])
        offset = symbol["st_value"] - section["sh_addr"]
        symbols.append(symbol)
        codes.append(section.data()[offset : offset + symbol["st_size"]])
    hashes = tuple(hashlib.sha256(code).hexdigest() for code in codes)
    if hashes not in reviewed:
        raise ValueError("Software AV1 decoder ABI differs from review")
    targets = []
    for name in ("dav1d_open", "dav1d_send_data", "dav1d_get_picture", "dav1d_close"):
        matches = table.get_symbol_by_name(name) or []
        if len(matches) != 1:
            raise ValueError("Software AV1 public C entry point differs from review")
        targets.append(matches[0]["st_value"])
    calls = []
    for function, offset, role, opcode in (
        (0, 0x8B, 0, 0xE8),
        (1, 0xC3, 1, 0xE8),
        (1, 0x139, 2, 0xE8),
        (2, 0x13, 3, 0xE9),
        (3, 0x17, 3, 0xE8),
        (4, 0xD, 3, 0xE8),
    ):
        call = symbols[function]["st_value"] + offset
        code = codes[function]
        if code[offset] != opcode or (
            call + 5 + struct.unpack_from("<i", code, offset + 1)[0] != targets[role]
        ):
            raise ValueError("Software AV1 decoder call site differs from review")
        calls.append(call)
    return {"abi": 1, "offsets": targets + calls, "function_sha256": list(hashes)}


def decoder_scope(elf):
    """Read the declared hardware flag from reviewed decoder implementations."""
    table = elf.get_section_by_name(".symtab")
    result = {}
    for name, symbol, pattern, hardware in (
        (
            "electron",
            "_ZNK7discord5media8electron20ElectronVideoDecoder14GetDecoderInfoEv",
            b"\xc6\x43\x18\x01",
            True,
        ),
        (
            "dav1d",
            "_ZNK6webrtc12_GLOBAL__N_112Dav1dDecoder14GetDecoderInfoEv",
            b"\xc6\x47\x18\x00",
            False,
        ),
        ("libvpx", "_ZNK6webrtc16LibvpxVp8Decoder14GetDecoderInfoEv", b"\xc6\x47\x18\x00", False),
    ):
        entries = table.get_symbol_by_name(symbol) or []
        if len(entries) != 1:
            continue
        entry = entries[0]
        section = elf.get_section(entry["st_shndx"])
        offset = entry["st_value"] - section["sh_addr"]
        code = section.data()[offset : offset + entry["st_size"]]
        if code.count(pattern) == 1 and code.endswith(b"\xc3"):
            result[name] = hardware
    return result


def write_scope(module: Path, directory: Path) -> dict:
    report = scope(module)
    write_json(directory / "encoder-scope.json", report)
    return report
