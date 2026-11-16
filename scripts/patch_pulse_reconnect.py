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
import shutil
import tempfile
from pathlib import Path

from native_client_2483 import STAGES
from patch_video_pacing import REVIEWED

PATCH = "reconnect-failed-pulse-soundshare-context-v1"
INPUT_SHA256, OUTPUT_SHA256, SITES = STAGES["pulse-reconnect"]


def restored(data):
    """Expose the original layout to earlier, idempotent preparation stages."""
    if hashlib.sha256(data).hexdigest() != OUTPUT_SHA256:
        return data
    result = bytearray(data)
    for offset, before, _after in SITES:
        result[offset : offset + len(before)] = before
    if hashlib.sha256(result).hexdigest() != INPUT_SHA256:
        raise ValueError("Pulse reconnect restoration differs from review")
    return bytes(result)


def corrected(data):
    digest = hashlib.sha256(data).hexdigest()
    if digest == OUTPUT_SHA256:
        return data
    if digest != INPUT_SHA256:
        if digest in {after for _before, after, _offset in REVIEWED}:
            return data
        raise ValueError("Unsupported Pulse reconnect module")
    result = bytearray(data)
    for offset, before, after in SITES:
        if len(before) != len(after) or data[offset : offset + len(before)] != before:
            raise ValueError("Pulse reconnect instructions differ from review")
        result[offset : offset + len(before)] = after
    if hashlib.sha256(result).hexdigest() != OUTPUT_SHA256:
        raise ValueError("Pulse reconnect output differs from review")
    return bytes(result)


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
        "supported": hashlib.sha256(result).hexdigest() == OUTPUT_SHA256,
        "changed_bytes": sum(a != b for a, b in zip(data, result, strict=True)),
    }
