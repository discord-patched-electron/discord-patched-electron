"""Read Windows Discord encoder property call sites without executing the module.

This records static evidence, not runtime encoder choices, effective driver
settings, successful codec negotiation or Windows image-quality measurements.
"""

import argparse
import bisect
import hashlib
import json
import struct
from pathlib import Path

from capstone import CS_ARCH_X86, CS_MODE_64, Cs
from capstone.x86 import X86_OP_MEM, X86_REG_RIP

PROPERTIES = (
    "TargetBitrate",
    "VBVBufferSize",
    "Usage",
    "RateControlMethod",
    "EnableVBAQ",
    "RateControlPreanalysisEnable",
    "QualityPreset",
    "HevcQualityPreset",
    "HevcRateControlMethod",
    "HevcUsage",
    "HevcVBVBufferSize",
    "Av1QualityPreset",
    "Av1RateControlMethod",
    "Av1Usage",
    "Av1VBVBufferSize",
    "Av1AlignmentMode",
    "Profile",
    "ProfileLevel",
    "MinQP",
    "MaxQP",
    "EnforceHRD",
    "BPicturesPattern",
    "BReferenceEnable",
    "MaxNumRefFrames",
    "LowLatencyInternal",
    "FillerDataEnable",
    "PeakBitrate",
    "InitialVBVBufferFullness",
    "MaxAUSize",
    "HevcEnableVBAQ",
    "HevcMaxNumRefFrames",
    "HevcProfile",
    "HevcTier",
    "HevcProfileLevel",
    "HevcMinQP_I",
    "HevcMinQP_P",
    "HevcEnforceHRD",
    "HevcRateControlPreAnalysisEnable",
    "HevcTargetBitrate",
    "HevcFrameRate",
    "HevcGOPSize",
    "HevcFillerDataEnable",
    "HevcMaxAUSize",
    "Av1Profile",
    "Av1Level",
    "Av1EncodingLatencyMode",
    "Av1EnforceHRD",
    "Av1RateControlSkipFrameEnable",
    "Av1TargetBitrate",
    "Av1FrameRate",
    "Av1GOPSize",
    "Av1QueryTimeout",
    "Av1WidthAlignmentFactor",
    "Av1HeightAlignmentFactor",
)
REVIEWED = {"6803596ec541909fa71a502d71dcf7d95b5c62f4cbb02d0d09bfbecaef4d7f3b"}


class PEImage:
    def __init__(self, data):
        if len(data) < 64 or len(data) > 128 * 1024 * 1024 or data[:2] != b"MZ":
            raise ValueError("Expected a bounded PE image")
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if pe + 24 + 240 > len(data) or data[pe : pe + 4] != b"PE\0\0":
            raise ValueError("Invalid PE header")
        machine, count = struct.unpack_from("<HH", data, pe + 4)
        size = struct.unpack_from("<H", data, pe + 20)[0]
        if machine != 0x8664 or size < 240 or struct.unpack_from("<H", data, pe + 24)[0] != 0x20B:
            raise ValueError("Expected x86-64 PE32+")
        if not 1 <= count <= 96 or pe + 24 + size + count * 40 > len(data):
            raise ValueError("Invalid PE section table")
        self.data = data
        self.base = struct.unpack_from("<Q", data, pe + 48)[0]
        self.sections = {}
        for i in range(count):
            at = pe + 24 + size + i * 40
            name = data[at : at + 8].rstrip(b"\0").decode("ascii")
            virtual_size, rva, raw_size, offset = struct.unpack_from("<IIII", data, at + 8)
            if offset + raw_size > len(data) or name in self.sections:
                raise ValueError("Invalid PE section extent")
            self.sections[name] = (rva, virtual_size, offset, raw_size)

    def address(self, offset):
        for rva, _, raw, size in self.sections.values():
            if raw <= offset < raw + size:
                return self.base + rva + offset - raw
        return None


def audit(data):
    image = PEImage(data)
    if ".text" not in image.sections or ".pdata" not in image.sections:
        raise ValueError("Encoder audit requires code and unwind bounds")
    rva, _, raw, size = image.sections[".text"]
    _, virtual_size, pdata, pdata_size = image.sections[".pdata"]
    if virtual_size > pdata_size or virtual_size % 12:
        raise ValueError("Invalid PE unwind table")
    bounds = [struct.unpack_from("<III", data, pdata + i)[:2] for i in range(0, virtual_size, 12)]
    if bounds != sorted(bounds) or any(
        a >= b
        or not any(
            section_rva <= a < b <= section_rva + section_size
            for section_rva, _, _, section_size in image.sections.values()
        )
        for a, b in bounds
    ):
        raise ValueError("Invalid PE function bounds")
    starts = [a for a, _ in bounds]
    properties = {}
    for name in PROPERTIES:
        term = name.encode()
        at = data.find(term)
        while at >= 0:
            address = image.address(at)
            if address is not None:
                properties[address] = name
            at = data.find(term, at + len(term))
    decoder = Cs(CS_ARCH_X86, CS_MODE_64)
    decoder.detail = True
    decoder.skipdata = True
    hits = []
    for instruction in decoder.disasm(data[raw : raw + size], image.base + rva):
        if not instruction.id:
            continue
        for operand in instruction.operands:
            if operand.type != X86_OP_MEM or operand.mem.base != X86_REG_RIP:
                continue
            target = instruction.address + instruction.size + operand.mem.disp
            if target in properties:
                hits.append((instruction.address - image.base, properties[target]))
                if len(hits) > 512:
                    raise ValueError("Encoder property reference count exceeds audit bounds")
    evidence = []
    functions = {}
    for target, name in hits:
        index = bisect.bisect_right(starts, target) - 1
        if index < 0:
            continue
        begin, end = bounds[index]
        if target >= end:
            continue
        code = list(decoder.disasm(data[raw + begin - rva : raw + end - rva], image.base + begin))
        indices = [
            i for i, instruction in enumerate(code) if instruction.address - image.base == target
        ]
        if not indices:
            continue
        # Retain complete bounded functions for conditional properties. A small
        # xref window alone can hide the FPS, geometry or latency branch.
        if end - begin <= 4096:
            functions[str(begin)] = {
                "end_rva": end,
                "instructions": [
                    {"rva": ins.address - image.base, "instruction": f"{ins.mnemonic} {ins.op_str}"}
                    for ins in code
                ],
            }
        index = indices[0]
        evidence.append(
            {
                "property": name,
                "rva": target,
                "function_rva": begin,
                "context": [
                    {"rva": ins.address - image.base, "instruction": f"{ins.mnemonic} {ins.op_str}"}
                    for ins in code[max(0, index - 16) : index + 12]
                ],
            }
        )
    digest = hashlib.sha256(data).hexdigest()
    return {
        "schema": 1,
        "sha256": digest,
        "reviewed_module": digest in REVIEWED,
        "runtime_tested": False,
        "effective_configuration_verified": False,
        "functions": functions,
        "property_references": evidence,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.module.stat().st_size > 128 * 1024 * 1024:
        parser.error("PE image exceeds the audit bound")
    args.output.write_text(json.dumps(audit(args.module.read_bytes()), indent=2) + "\n")
