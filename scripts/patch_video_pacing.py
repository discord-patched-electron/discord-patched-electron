"""Bound scene-change queuing in the reviewed screenshare ALR default.

Screenshare probing overrides the general pacer defaults with 1.0x pacing and a
2875 ms queue limit. Restore regular WebRTC burst headroom (2.5x) and a 50 ms
queue budget. Explicit field trials, congestion feedback and encoder allocation
remain native. Only the default ASCII policy changes; no code is redirected.
"""

import hashlib
import shutil
import tempfile
from pathlib import Path

from native_client_2444 import STAGES
from native_client_2472 import STAGES as CLIENT_2472_STAGES
from native_client_2483 import STAGES as CLIENT_2483_STAGES

PATCH = "screenshare-pacing-default-v1"
BEFORE = b"1.0,2875,80,40,-60,3\0"
AFTER = b"2.5,0050,80,40,-60,3\0"
REVIEWED = (
    (*CLIENT_2483_STAGES["pacing"][0:2], CLIENT_2483_STAGES["pacing"][2][0][0]),
    (*CLIENT_2472_STAGES["pacing"][0:2], CLIENT_2472_STAGES["pacing"][2][0][0]),
    (*STAGES["pacing"][0:2], STAGES["pacing"][2][0][0]),
    (
        "eee57a64d3342690cbf1edc70576f7d10b956758636396a052242b6b06421462",
        "0332f1c45a373e019c20655c76dd77258876ee02d10129d4b4b2857be67c270a",
        0x188CD44,
    ),
    (
        "b082fda5450887f76208232b81f83dc753d8f7ae2e302e5573d56af9b72b0570",
        "7891b5ef54457b80df036a8b38d59045e9dbcca881d8c8b66f29e88fd050f479",
        0x1888944,
    ),
)
OUTPUT_BASES = {after: before for before, after, _ in REVIEWED}


def corrected(data):
    digest = hashlib.sha256(data).hexdigest()
    for before, after, offset in REVIEWED:
        if digest == after:
            return data
        if digest != before:
            continue
        if data[offset : offset + len(BEFORE)] != BEFORE or data.count(BEFORE) != 1:
            raise ValueError("Native screenshare pacing default differs from review")
        result = data[:offset] + AFTER + data[offset + len(BEFORE) :]
        if hashlib.sha256(result).hexdigest() != after:
            raise ValueError("Native screenshare pacing output differs from review")
        return result
    raise ValueError("Unreviewed native video pacing module")


def patch(binary: Path):
    data = binary.read_bytes()
    result = corrected(data)
    if result != data:
        with tempfile.TemporaryDirectory(dir=binary.parent) as temporary:
            target = Path(temporary) / binary.name
            shutil.copy2(binary, target)
            target.write_bytes(result)
            target.replace(binary)
    return {
        "patch": PATCH,
        "input_sha256": hashlib.sha256(data).hexdigest(),
        "output_sha256": hashlib.sha256(result).hexdigest(),
        "already_patched": result == data,
        "changed_bytes": sum(a != b for a, b in zip(data, result, strict=True)),
    }
