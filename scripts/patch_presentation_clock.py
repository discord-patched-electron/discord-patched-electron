"""Use the reviewed VideoFrame microsecond clock at Discord's direct output ABI.

The old instruction reads the 32-bit RTP clock at +0x10. The external DiscordFrame
ABI requires a signed 64-bit microsecond timestamp, which VideoFrame stores at
+0x20 (also verified by render_time_ms). No codec or pixel path is changed.
"""

import hashlib
import shutil
import tempfile
from pathlib import Path

from native_client_2444 import STAGES
from native_client_2472 import STAGES as CLIENT_2472_STAGES
from native_client_2483 import STAGES as CLIENT_2483_STAGES
from patch_video_pacing import OUTPUT_BASES as PACED_BASES

PATCH = "direct-video-microsecond-clock-v1"
BEFORE = bytes.fromhex("418b46104889442428")
AFTER = bytes.fromhex("498b46204889442428")
REVIEWED = (
    (*CLIENT_2483_STAGES["clock"][0:2], CLIENT_2483_STAGES["clock"][2][0][0]),
    (*CLIENT_2472_STAGES["clock"][0:2], CLIENT_2472_STAGES["clock"][2][0][0]),
    (*STAGES["clock"][0:2], STAGES["clock"][2][0][0]),
    (
        "85baa5b9c9c597abcce5182249dd7b551c60157d3061c321ad6aed3d433ed008",
        "eee57a64d3342690cbf1edc70576f7d10b956758636396a052242b6b06421462",
        0x44D0B0,
    ),
    (
        "8510b483b6618f171da32013cb768be45470bdc517534a1ed861a8d946a120fa",
        "b082fda5450887f76208232b81f83dc753d8f7ae2e302e5573d56af9b72b0570",
        0x44A9C0,
    ),
)
OUTPUT_BASES = {after: before for before, after, _ in REVIEWED}
OUTPUT_BASES.update({after: OUTPUT_BASES[before] for after, before in PACED_BASES.items()})


def corrected(data):
    digest = hashlib.sha256(data).hexdigest()
    if digest in PACED_BASES:
        return data
    for before, after, offset in REVIEWED:
        if digest == after:
            return data
        if digest != before:
            continue
        if data[offset : offset + len(BEFORE)] != BEFORE:
            raise ValueError("Direct presentation clock instructions differ from review")
        result = data[:offset] + AFTER + data[offset + len(BEFORE) :]
        if hashlib.sha256(result).hexdigest() != after:
            raise ValueError("Direct presentation clock output differs from review")
        return result
    raise ValueError("Unreviewed native presentation clock module")


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
