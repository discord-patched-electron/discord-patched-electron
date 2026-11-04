"""Rebuild the project-owned capture shader; Electron is not compiled.

Development tool only: glslangValidator and spirv-opt/spirv-val are required.
The padded debug string preserves the reviewed native embedded-slice length.
"""

import argparse
import struct
import subprocess
import tempfile
from pathlib import Path

from common import ROOT

SIZE = 9272


def fit(data):
    words = list(struct.unpack(f"<{len(data) // 4}I", data))
    if words[0] != 0x07230203 or len(data) % 4:
        raise ValueError("Invalid SPIR-V header")
    extra = (SIZE - len(data)) // 4
    if not 3 <= extra < 65536:
        raise ValueError("Shader does not fit the reviewed embedded slice")
    # OpString belongs before annotations/types. No executable padding and no
    # change to the native Vec/slice length or its enclosing ELF sections.
    offset = 5
    while offset < len(words) and words[offset] & 65535 not in (71, 72, 19):
        count = words[offset] >> 16
        if not count:
            raise ValueError("Invalid SPIR-V instruction")
        offset += count
    text = b" " * ((extra - 2) * 4 - 1) + b"\0"
    filler = [(extra << 16) | 7, words[3], *struct.unpack(f"<{extra - 2}I", text)]
    words[3] += 1
    words[offset:offset] = filler
    return struct.pack(f"<{len(words)}I", *words)


def build(output):
    with tempfile.TemporaryDirectory() as temporary:
        raw, stripped = Path(temporary) / "raw.spv", Path(temporary) / "stripped.spv"
        subprocess.run(
            [
                "glslangValidator",
                "-V",
                "--target-env",
                "vulkan1.1",
                "-Os",
                str(ROOT / "integration/video/shaders/chroma-420.comp"),
                "-o",
                str(raw),
            ],
            check=True,
        )
        subprocess.run(["spirv-opt", "--strip-debug", str(raw), "-o", str(stripped)], check=True)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(fit(stripped.read_bytes()))
        subprocess.run(["spirv-val", "--target-env", "vulkan1.1", str(output)], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output)
