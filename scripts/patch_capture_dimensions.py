"""Preserve visible GPU capture dimensions in reviewed native modules.

Apply after patch_vaapi_hrd during private preparation. The native NV12 allocator
already aligns row pitch independently, and the fused shader bounds-checks its
rounded-up dispatch. Rounding visible dimensions to 64 instead changes aspect
ratio and exceeds WebRTC's requested pixel budget, forcing CPU CropAndScale.

This changes only the final rounding to the even dimensions required by 4:2:0.
It preserves the native aspect-fit/crop calculation, 4096 limit, allocator,
shader, metadata layout and ABI. Unknown modules require a fresh review.
"""

import hashlib
import shutil
import tempfile
from pathlib import Path

import native_client_2444
import native_client_2472
import patch_capture_chroma as chroma
import patch_decoder_eagain as decoder

INPUT_SHA256 = "0eecfa64b0120ca85c6454a39cf011f1f52d5661b33868ada0822580e644490e"
OUTPUT_SHA256 = "23dae94b2adcc295faa473fd5869b17cbc19154eec50d6cdf5970ef4f921b0c5"
PINNED_INPUT_SHA256 = "62812b1287d5ea13fd762e91c18a20532734080cd3b30f0059b538281c0c4f64"
PINNED_OUTPUT_SHA256 = "72096efc87a407b975e9913477d387e2de9a1f1028575c1f97a22365bacb007c"
# The same two reviewed modules with the independently reviewed microphone
# hotplug guard applied by patch_pulse_audio. Keep complete hashes at each stage.
VOLUME_INPUT_SHA256 = "6c2c26fcb494f5b5656f7e69bbf9b7111466952157617621a3696c8c689021bb"
VOLUME_OUTPUT_SHA256 = "3b1c7da666ecba5ef31d5fbbf68fe569cdf8384549f8803d691ca6ada0af4f85"
PINNED_VOLUME_INPUT_SHA256 = "838a77d7498201d9c0ca1f1f7e0d5fe8ce54d6a3271f729b630c1128fb4ce26c"
PINNED_VOLUME_OUTPUT_SHA256 = "0682ab8c93c51c8c36debc102c829b4177f16ff4fae4cab2e901d6ee341461e4"
PATCH = "linux-gpu-capture-visible-dimensions-v1"

# EAX=width, ECX=height at calculate_pool_resolution's common return. Original:
# add $32, eax; and $-64, eax; add $32, ecx; and $-64, ecx.
# Replacement: add $1, eax; and $-2, eax; add $1, ecx; and $-2, ecx.
BEFORE = bytes.fromhex("83c02083e0c083c12083e1c0")
AFTER = bytes.fromhex("83c00183e0fe83c10183e1fe")
OFFSET = 0x157408E
PINNED_OFFSET = 0x15719AE


def reviewed(digest):
    current = native_client_2472.reviewed("dimensions", digest) or native_client_2444.reviewed(
        "dimensions", digest
    )
    if current:
        before, after, sites = current
        return before, after, sites[0][0]
    base = chroma.SCOPE_BASES.get(digest)
    if base in (decoder.OUTPUT_SHA256, decoder.PINNED_OUTPUT_SHA256):
        return digest, digest, OFFSET if base == decoder.OUTPUT_SHA256 else PINNED_OFFSET
    for before, after, offset in (
        (decoder.OUTPUT_SHA256, decoder.OUTPUT_SHA256, OFFSET),
        (decoder.PINNED_OUTPUT_SHA256, decoder.PINNED_OUTPUT_SHA256, PINNED_OFFSET),
        (INPUT_SHA256, OUTPUT_SHA256, OFFSET),
        (PINNED_INPUT_SHA256, PINNED_OUTPUT_SHA256, PINNED_OFFSET),
        (VOLUME_INPUT_SHA256, VOLUME_OUTPUT_SHA256, OFFSET),
        (PINNED_VOLUME_INPUT_SHA256, PINNED_VOLUME_OUTPUT_SHA256, PINNED_OFFSET),
    ):
        if digest in (before, after):
            return before, after, offset
    raise ValueError("Unsupported GPU capture module; review its dimension and allocation paths")


def corrected(data):
    digest = hashlib.sha256(data).hexdigest()
    _, expected, offset = reviewed(digest)
    if digest == expected:
        return data
    if len(BEFORE) != len(AFTER) or data[offset : offset + len(BEFORE)] != BEFORE:
        raise ValueError("GPU capture dimension instructions differ from the reviewed module")
    result = data[:offset] + AFTER + data[offset + len(BEFORE) :]
    if hashlib.sha256(result).hexdigest() != expected:
        raise ValueError("GPU capture dimension correction did not match the reviewed output")
    return result


def patch(binary: Path) -> dict:
    original = binary.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    _, expected, _ = reviewed(digest)
    candidate = corrected(original)
    already = candidate == original
    if not already:
        # Preserve shared source inodes and active mappings; replace only the
        # privately prepared file after validating the complete written output.
        with tempfile.TemporaryDirectory(dir=binary.parent) as temporary:
            target = Path(temporary) / binary.name
            shutil.copy2(binary, target)
            target.write_bytes(candidate)
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError("GPU capture dimension written output failed validation")
            target.replace(binary)
    return {
        "patch": PATCH,
        "input_sha256": digest,
        "output_sha256": expected,
        "already_patched": already,
        "changed_bytes": 0 if already else sum(a != b for a, b in zip(BEFORE, AFTER, strict=True)),
    }
