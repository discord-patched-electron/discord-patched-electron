"""Recreate a failed soundshare Pulse context on the next Attach.

Canary 1.0.2483's PulseConnect returns whenever a mainloop exists, including
after its context has failed. PID/source changes cannot recover that context.
The replacement keeps healthy/connecting contexts, and rebuilds failed or
terminated contexts under the existing recursive mainloop lock. Detach clears
the old stream maps first. The loop, name, callbacks, connect flags and ABI stay
the same. Cold startup starts the loop once before creating the context under
its lock. The replacement fits inside PulseConnect; its unwind program is
updated in place. Retry policy belongs to the application-audio controller.

Whole-module hashes bind the code, unwind data, branch/call targets, controller
layout and libpulse PLT relocations. Older reviewed clients retain
their original connection behavior until separately ported and qualified.
"""

import hashlib
from pathlib import Path

from common import write_verified_binary
from native_client_2483 import STAGES
from native_client_2484 import STAGES as CLIENT_2484_STAGES
from native_client_2510 import STAGES as CLIENT_2510_STAGES
from patch_video_pacing import REVIEWED

PATCH = "reconnect-failed-pulse-soundshare-context-v1"
INPUT_SHA256, OUTPUT_SHA256, SITES = STAGES["pulse-reconnect"]


def chains():
    return (
        (INPUT_SHA256, OUTPUT_SHA256, SITES),
        CLIENT_2484_STAGES["pulse-reconnect"],
        CLIENT_2510_STAGES["pulse-reconnect"],
    )


def restored(data):
    """Expose the original layout to earlier, idempotent preparation stages."""
    digest = hashlib.sha256(data).hexdigest()
    for original, final, sites in chains():
        if digest != final:
            continue
        result = bytearray(data)
        for offset, before, _after in sites:
            result[offset : offset + len(before)] = before
        if hashlib.sha256(result).hexdigest() != original:
            raise ValueError("Pulse reconnect restoration differs from review")
        return bytes(result)
    return data


def corrected(data):
    digest = hashlib.sha256(data).hexdigest()
    for original, final, sites in chains():
        if digest == final:
            return data
        if digest != original:
            continue
        result = bytearray(data)
        for offset, before, after in sites:
            if len(before) != len(after) or data[offset : offset + len(before)] != before:
                raise ValueError("Pulse reconnect instructions differ from review")
            result[offset : offset + len(before)] = after
        if hashlib.sha256(result).hexdigest() != final:
            raise ValueError("Pulse reconnect output differs from review")
        return bytes(result)
    if digest in {after for _before, after, _offset in REVIEWED}:
        return data
    raise ValueError("Unsupported Pulse reconnect module")


def patch(binary: Path):
    data = binary.read_bytes()
    result = corrected(data)
    before, after = hashlib.sha256(data).hexdigest(), hashlib.sha256(result).hexdigest()
    changes = 0
    if result is not data:
        _, _, sites = next(chain for chain in chains() if chain[0] == before)
        changes = sum(
            old != new
            for _, original, replacement in sites
            for old, new in zip(original, replacement, strict=True)
        )
        write_verified_binary(binary, result, after)
    return {
        "patch": PATCH,
        "input_sha256": before,
        "output_sha256": after,
        "already_patched": result is data,
        "supported": after in {final for _, final, _ in chains()},
        "changed_bytes": changes,
    }
