"""Treat buffered FFmpeg decoder output as NO_OUTPUT in two reviewed modules.

Apply after the Pulse, HRD and capture-dimension patches. The complete-file
identities bind the error block, all branch/call targets and packet cleanup.
The success path and every error except receive_frame AVERROR(EAGAIN) are
unchanged. In particular send_packet EAGAIN remains an error.
"""

import hashlib
import shutil
import tempfile
from pathlib import Path

import patch_capture_chroma as chroma

PATCH = "ffmpeg-decoder-buffered-output-v1"

INPUT_SHA256 = "3b1c7da666ecba5ef31d5fbbf68fe569cdf8384549f8803d691ca6ada0af4f85"
OUTPUT_SHA256 = "f5dae8fc205c8123ceff2f520dfcd50d8f2357de8db221cb5215a92abbcac2e9"
SITES = (
    (0x44C802, bytes.fromhex("7850"), bytes.fromhex("7842")),
    (
        0x44C829,
        bytes.fromhex(
            "89c5e890cbf2ff84c00f85ca000000488d3d2c7a3e01488d35fe433f01488d0d55463f01ba7b120000eb2989c5e865cbf2ff84c00f859f000000488d3d017a3e01488d35d3433f01488d0d46463f01bab31200004189e831c0e8a9dc2400eb79"
        ),
        bytes.fromhex(
            "89c5e890cbf2ff84c00f85ca000000488d0d63463f01ba7b120000eb2983f8f57509448d780ce9bc00000089c5e865cbf2ff84c00f859f000000488d0d54463f01bab3120000488d3df5793e01488d35c7433f014189e831c0e8a9dc2400eb79"
        ),
    ),
)

PINNED_INPUT_SHA256 = "0682ab8c93c51c8c36debc102c829b4177f16ff4fae4cab2e901d6ee341461e4"
PINNED_OUTPUT_SHA256 = "83f278107c08b7717ce9f9c147ee5e161049f1626773d054d514ce98e9223977"
PINNED_SITES = (
    (0x44A112, bytes.fromhex("7850"), bytes.fromhex("7842")),
    (
        0x44A139,
        bytes.fromhex(
            "89c5e880ecf2ff84c00f85ca000000488d3d0c613e01488d35ce273f01488d0d252a3f01ba7b120000eb2989c5e855ecf2ff84c00f859f000000488d3de1603e01488d35a3273f01488d0d162a3f01bab31200004189e831c0e879cf2400eb79"
        ),
        bytes.fromhex(
            "89c5e880ecf2ff84c00f85ca000000488d0d332a3f01ba7b120000eb2983f8f57509448d780ce9bc00000089c5e855ecf2ff84c00f859f000000488d0d242a3f01bab3120000488d3dd5603e01488d3597273f014189e831c0e879cf2400eb79"
        ),
    ),
)

# The only non-decoder edits relative to these previously scoped modules are
# the separately reviewed microphone guard. No capture or encoder ABI changes.
SCOPE_BASES = {
    INPUT_SHA256: "23dae94b2adcc295faa473fd5869b17cbc19154eec50d6cdf5970ef4f921b0c5",
    OUTPUT_SHA256: "23dae94b2adcc295faa473fd5869b17cbc19154eec50d6cdf5970ef4f921b0c5",
    PINNED_INPUT_SHA256: "72096efc87a407b975e9913477d387e2de9a1f1028575c1f97a22365bacb007c",
    PINNED_OUTPUT_SHA256: "72096efc87a407b975e9913477d387e2de9a1f1028575c1f97a22365bacb007c",
}


def extend_reviewed(reviewed):
    """Reuse ABI review only for exact independently reviewed final modules."""
    if isinstance(reviewed, set):
        reviewed.update(digest for digest, base in SCOPE_BASES.items() if base in reviewed)
    else:
        reviewed.update(
            {digest: reviewed[base] for digest, base in SCOPE_BASES.items() if base in reviewed}
        )
    chroma.extend_reviewed(reviewed)


def reviewed(digest):
    base = chroma.SCOPE_BASES.get(digest)
    if base in (OUTPUT_SHA256, PINNED_OUTPUT_SHA256):
        return digest, digest, SITES if base == OUTPUT_SHA256 else PINNED_SITES
    for before, after, sites in (
        (INPUT_SHA256, OUTPUT_SHA256, SITES),
        (PINNED_INPUT_SHA256, PINNED_OUTPUT_SHA256, PINNED_SITES),
    ):
        if digest in (before, after):
            return before, after, sites
    raise ValueError("Unsupported buffered decoder module; review its Decode error paths")


def corrected(data):
    digest = hashlib.sha256(data).hexdigest()
    _, expected, sites = reviewed(digest)
    if digest == expected:
        return data
    result = bytearray(data)
    for offset, before, after in sites:
        if len(before) != len(after) or data[offset : offset + len(before)] != before:
            raise ValueError("Decoder instructions differ from the reviewed module")
        result[offset : offset + len(before)] = after
    if hashlib.sha256(result).hexdigest() != expected:
        raise ValueError("Decoder correction did not match the reviewed output")
    return bytes(result)


def patch(binary: Path) -> dict:
    original = binary.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    _, expected, sites = reviewed(digest)
    candidate = corrected(original)
    already = candidate == original
    if not already:
        with tempfile.TemporaryDirectory(dir=binary.parent) as temporary:
            target = Path(temporary) / binary.name
            shutil.copy2(binary, target)
            target.write_bytes(candidate)
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError("Decoder written output failed validation")
            target.replace(binary)
    return {
        "patch": PATCH,
        "input_sha256": digest,
        "output_sha256": expected,
        "already_patched": already,
        "changed_bytes": 0
        if already
        else sum(sum(a != b for a, b in zip(old, new, strict=True)) for _, old, new in sites),
    }
