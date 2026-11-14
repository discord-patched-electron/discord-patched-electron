"""Bind the explicit-sync absolute-deadline fix to reviewed native code."""

import hashlib
import struct
from pathlib import Path

from common import sha256, write_json
from elftools.elf.elffile import ELFFile
from native_client_2444 import HASHES as CLIENT_2444_HASHES
from native_client_2472 import HASHES as CLIENT_2472_HASHES
from native_client_2483 import HASHES as CLIENT_2483_HASHES
from patch_capture_dimensions import OUTPUT_SHA256 as DIMENSIONS_OTHER_SHA256
from patch_capture_dimensions import PINNED_OUTPUT_SHA256 as DIMENSIONS_SHA256
from patch_decoder_eagain import extend_reviewed
from patch_vaapi_hrd import INPUT_SHA256, OUTPUT_SHA256, PINNED_INPUT_SHA256, PINNED_OUTPUT_SHA256

REVIEWED = {
    **dict.fromkeys(
        (PINNED_INPUT_SHA256, PINNED_OUTPUT_SHA256, DIMENSIONS_SHA256),
        (
            "7fc0dcadae54a1342499f55ff95dc69d29b4103099f0c6d8b98dce84c467eb56",
            "358e25c86932b0e906378f81789c752586486d2038722223de72824daa7bf802",
        ),
    ),
    **dict.fromkeys(
        (INPUT_SHA256, OUTPUT_SHA256, DIMENSIONS_OTHER_SHA256),
        (
            "2685487e7fd0ac43d38a92118619c420bda89603fc2b988ea62d7b0a8b1cdf4b",
            "244c8d103ec36aab0fc655674d839ebf09be8de367e74a2c5c8b9a7382530277",
        ),
    ),
}
REVIEWED.update(
    dict.fromkeys(
        CLIENT_2444_HASHES,
        (
            "bb06b67b4698498782595a0c07a68331ffc186e4c10cb2bbc7dd1d1a25ea16c3",
            "cfb5133104d98dd39647b158c56a7be22bfed2540c6e3620838ff0bd5274bc6d",
        ),
    )
)
REVIEWED.update(
    dict.fromkeys(
        CLIENT_2472_HASHES,
        (
            "6aab0278c3ab1a909132cbb0813342f1c01049eeaaf7d75c12e72e1fff986a37",
            "364b3f5741a69b6aed47655e232e65cbb6370a7ebd49c5dc1a04acfe80be4378",
        ),
    )
)
REVIEWED.update(
    dict.fromkeys(
        CLIENT_2483_HASHES,
        (
            "74a02f8678f787c6e499482a68500e34385f51de7e161047d3c7029a63fc7648",
            "9012b61a986d984f6f7f173b6a9e837bcba1f09b7487f476d52500d137a446ce",
        ),
    )
)
extend_reviewed(REVIEWED)
CONTEXT = bytes.fromhex("48b900f2052a0100000089ef4489f64c89fa")


def scope(module: Path) -> dict:
    digest = sha256(module)
    if digest not in REVIEWED:
        return {"schema": 1, "supported": False, "reason": "unreviewed-native-module"}
    with module.open("rb") as stream:
        elf = ELFFile(stream)
        table = elf.get_section_by_name(".symtab")
        if elf["e_machine"] != "EM_X86_64" or not table:
            raise ValueError("Reviewed capture synchronization requires x86-64 symbols")
        selected = []
        for suffix in ("26execute_with_explicit_sync", "29wait_timeline_point_available"):
            matches = [symbol for symbol in table.iter_symbols() if symbol.name.endswith(suffix)]
            if len(matches) != 1:
                raise ValueError("Capture synchronization symbols differ from review")
            selected.append(matches[0])
        function, target = selected
        codes = []
        for symbol, expected in zip(selected, REVIEWED[digest], strict=True):
            section = elf.get_section(symbol["st_shndx"])
            offset = symbol["st_value"] - section["sh_addr"]
            code = section.data()[offset : offset + symbol["st_size"]]
            if hashlib.sha256(code).hexdigest() != expected:
                raise ValueError("Capture synchronization function differs from review")
            codes.append(code)
        code = codes[0]
        call = function["st_value"] + 0x79
        if (
            code[0x67:0x79] != CONTEXT
            or code[0x79] != 0xE8
            or call + 5 + struct.unpack_from("<i", code, 0x7A)[0] != target["st_value"]
        ):
            raise ValueError("Capture synchronization call context differs from review")
        return {
            "schema": 1,
            "supported": True,
            "voice_sha256": digest,
            "function_sha256": REVIEWED[digest][0],
            "target_sha256": REVIEWED[digest][1],
            "call": call,
            "target": target["st_value"],
        }


def write_scope(module: Path, directory: Path) -> dict:
    report = scope(module)
    write_json(directory / "capture-sync-scope.json", report)
    return report
