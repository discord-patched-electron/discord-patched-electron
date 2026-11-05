"""Filter GPU 4:2:0 chroma in reviewed private native modules.

The project-owned replacement keeps the embedded SPIR-V byte length, descriptor
bindings, push constants and one-dispatch pipeline. Full-file identities bind
the edit to modules after the Pulse, HRD, dimensions and decoder corrections.
"""

import hashlib
import shutil
import tempfile
from pathlib import Path

import native_client_2444
from common import ROOT
from patch_presentation_clock import OUTPUT_BASES

PATCH = "gpu-capture-chroma-box-filter-v1"
INPUT_SHA256 = "f5dae8fc205c8123ceff2f520dfcd50d8f2357de8db221cb5215a92abbcac2e9"
OUTPUT_SHA256 = "85baa5b9c9c597abcce5182249dd7b551c60157d3061c321ad6aed3d433ed008"
OFFSET = 0x19438A4
PINNED_INPUT_SHA256 = "83f278107c08b7717ce9f9c147ee5e161049f1626773d054d514ce98e9223977"
PINNED_OUTPUT_SHA256 = "8510b483b6618f171da32013cb768be45470bdc517534a1ed861a8d946a120fa"
PINNED_OFFSET = 0x1941434
SHADER_SIZE = 9272
BEFORE_SHADER = "aadde83936f48237cd08ba2bb781ffc576c3245a227edf50dda8fdd23f36284a"
AFTER_SHADER = "bf4ccb07769d2ccdb179ead82ae210d7c9176452086d0d2ede1e028c0ffbc1c1"
SCOPE_BASES = {OUTPUT_SHA256: INPUT_SHA256, PINNED_OUTPUT_SHA256: PINNED_INPUT_SHA256}
SCOPE_BASES.update(
    {output: SCOPE_BASES[base] for output, base in OUTPUT_BASES.items() if base in SCOPE_BASES}
)


def extend_reviewed(reviewed):
    # Only shader data changed; native function code and all layouts are intact.
    if isinstance(reviewed, set):
        reviewed.update(digest for digest, base in SCOPE_BASES.items() if base in reviewed)
    else:
        reviewed.update(
            {digest: reviewed[base] for digest, base in SCOPE_BASES.items() if base in reviewed}
        )


def reviewed(digest):
    current = native_client_2444.reviewed("chroma", digest)
    if current:
        before, after, sites = current
        return before, after, sites[0][0]
    if digest in OUTPUT_BASES:
        return digest, digest, 0
    for before, after, offset in (
        (INPUT_SHA256, OUTPUT_SHA256, OFFSET),
        (PINNED_INPUT_SHA256, PINNED_OUTPUT_SHA256, PINNED_OFFSET),
    ):
        if digest in (before, after):
            return before, after, offset
    raise ValueError("Unsupported GPU chroma module; review its embedded shader and interface")


def corrected(data):
    digest = hashlib.sha256(data).hexdigest()
    _, expected, offset = reviewed(digest)
    if digest == expected:
        return data
    shader = (ROOT / "integration/video/shaders/chroma-420.spv").read_bytes()
    if len(shader) != SHADER_SIZE or hashlib.sha256(shader).hexdigest() != AFTER_SHADER:
        raise ValueError("Replacement GPU chroma shader differs from the reviewed asset")
    if hashlib.sha256(data[offset : offset + SHADER_SIZE]).hexdigest() != BEFORE_SHADER:
        raise ValueError("Embedded GPU chroma shader differs from the reviewed module")
    result = data[:offset] + shader + data[offset + SHADER_SIZE :]
    if hashlib.sha256(result).hexdigest() != expected:
        raise ValueError("GPU chroma correction did not match the reviewed output")
    return result


def patch(binary: Path) -> dict:
    original = binary.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    _, expected, _ = reviewed(digest)
    candidate = corrected(original)
    already = candidate == original
    if not already:
        with tempfile.TemporaryDirectory(dir=binary.parent) as temporary:
            target = Path(temporary) / binary.name
            shutil.copy2(binary, target)
            target.write_bytes(candidate)
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError("GPU chroma written output failed validation")
            target.replace(binary)
    return {
        "patch": PATCH,
        "input_sha256": digest,
        "output_sha256": expected,
        "already_patched": already,
        "changed_bytes": 0
        if already
        else sum(a != b for a, b in zip(original, candidate, strict=True)),
    }
