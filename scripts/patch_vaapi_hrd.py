"""Correct reviewed H.264 VA-API HRD arguments during private module preparation.

Full-file hashes bind the edit to reviewed code, field layouts and call targets.
Run after the PulseAudio patch. Unknown modules fail compatibility preparation;
the updater retains its previous generation. No live process is modified.
"""

import hashlib
import shutil
import tempfile
from pathlib import Path

import native_client_2444
import patch_capture_chroma as chroma
import patch_capture_dimensions as dimensions
import patch_decoder_eagain as decoder

INPUT_SHA256 = "f56d3c31611f769e6798bb2bc03ad2b5a21e2b29224825ca740af013e145f0d8"
OUTPUT_SHA256 = "0eecfa64b0120ca85c6454a39cf011f1f52d5661b33868ada0822580e644490e"
PATCH = "h264-vaapi-hrd-argument-order-v1"
SITES = (
    # AMD surface backend: load [rax+0x38c].
    (
        0x14C2790,
        bytes.fromhex("8bb88c0300008d347fc1ee02"),
        bytes.fromhex("8bb08c0300008d3c76c1ef02"),
    ),
    # Vulkan surface backend: load [rbp+0x3ec].
    (
        0x14C54AA,
        bytes.fromhex("8bbdec0300008d347fc1ee02"),
        bytes.fromhex("8bb5ec0300008d3c76c1ef02"),
    ),
)
# Canary 1.0.2357 in clients.lock.json. The complete normalized H.264
# encode_slice bodies and HRD constructor match the first reviewed module;
# their indirect call relocations resolve to that constructor in each binary.
PINNED_INPUT_SHA256 = "2352578d855e5ff75c0433fe8ca0ffa6d599691a506a7908da3d0d6523cc91b8"
PINNED_OUTPUT_SHA256 = "62812b1287d5ea13fd762e91c18a20532734080cd3b30f0059b538281c0c4f64"
PINNED_SITES = tuple(
    (offset, before, after)
    for offset, (_, before, after) in zip((0x14D8680, 0x14DB39A), SITES, strict=True)
)


# Complete-file identities after the microphone hotplug guard. Only the known
# guard's bytes differ; HRD instruction sites and expected output remain exact.
VOLUME_INPUT_SHA256 = "5dba9293886472c4f4bb75c3e64692140c5e4b9ca9a87e4b37fdf19af26bbc5b"
VOLUME_OUTPUT_SHA256 = "6c2c26fcb494f5b5656f7e69bbf9b7111466952157617621a3696c8c689021bb"
PINNED_VOLUME_INPUT_SHA256 = "6c63d839564ac29f69b6f8b80417e9c13089cdccdf69796b0254003417dd004b"
PINNED_VOLUME_OUTPUT_SHA256 = "838a77d7498201d9c0ca1f1f7e0d5fe8ce54d6a3271f729b630c1128fb4ce26c"


def reviewed(digest):
    current = native_client_2444.reviewed("hrd", digest)
    if current:
        before, after, sites = current
        return before, after, sites
    base = chroma.SCOPE_BASES.get(digest)
    if base in (decoder.OUTPUT_SHA256, decoder.PINNED_OUTPUT_SHA256):
        return digest, digest, SITES if base == decoder.OUTPUT_SHA256 else PINNED_SITES
    for before, after, sites in (
        (decoder.OUTPUT_SHA256, decoder.OUTPUT_SHA256, SITES),
        (decoder.PINNED_OUTPUT_SHA256, decoder.PINNED_OUTPUT_SHA256, PINNED_SITES),
        (INPUT_SHA256, OUTPUT_SHA256, SITES),
        (PINNED_INPUT_SHA256, PINNED_OUTPUT_SHA256, PINNED_SITES),
        (VOLUME_INPUT_SHA256, VOLUME_OUTPUT_SHA256, SITES),
        (PINNED_VOLUME_INPUT_SHA256, PINNED_VOLUME_OUTPUT_SHA256, PINNED_SITES),
        # A fully prepared module already has this HRD correction. Recognize
        # only the exact reviewed final hashes and preserve its dimension fix.
        (dimensions.OUTPUT_SHA256, dimensions.OUTPUT_SHA256, SITES),
        (dimensions.PINNED_OUTPUT_SHA256, dimensions.PINNED_OUTPUT_SHA256, PINNED_SITES),
        (dimensions.VOLUME_OUTPUT_SHA256, dimensions.VOLUME_OUTPUT_SHA256, SITES),
        (
            dimensions.PINNED_VOLUME_OUTPUT_SHA256,
            dimensions.PINNED_VOLUME_OUTPUT_SHA256,
            PINNED_SITES,
        ),
    ):
        if digest in (before, after):
            return before, after, sites
    raise ValueError("Unsupported H.264 VA-API module; review its HRD layout before preparing it")


def corrected(data):
    digest = hashlib.sha256(data).hexdigest()
    _, expected, sites = reviewed(digest)
    if digest == expected:
        return data
    result = bytearray(data)
    for offset, before, after in sites:
        if len(before) != len(after) or data[offset : offset + len(before)] != before:
            raise ValueError("H.264 HRD instructions differ from the reviewed module")
        result[offset : offset + len(before)] = after
    if hashlib.sha256(result).hexdigest() != expected:
        raise ValueError("H.264 HRD correction did not match the reviewed output")
    return bytes(result)


def patch(binary: Path) -> dict:
    original = binary.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    _, expected, sites = reviewed(digest)
    candidate = corrected(original)
    already = candidate == original
    if not already:
        # Replace only the prepared file, preserving mode and any shared source
        # inode. Never rewrite bytes through a mapped or hardlinked input inode.
        with tempfile.TemporaryDirectory(dir=binary.parent) as temporary:
            target = Path(temporary) / binary.name
            shutil.copy2(binary, target)
            target.write_bytes(candidate)
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError("H.264 HRD written output failed validation")
            target.replace(binary)
    return {
        "patch": PATCH,
        "input_sha256": digest,
        "output_sha256": expected,
        "already_patched": already,
        "changed_bytes": 0
        if already
        else sum(sum(a != b for a, b in zip(old, new, strict=True)) for _, old, new in sites),
        "hardware_performance_validated": False,
    }
