"""Build bounded GPU monitor thumbnails without capturing a screen."""

import argparse
import shutil
from pathlib import Path

from build_rust_support import build as build_rust
from common import sha256, write_json


def build(output):
    output.mkdir(parents=True, exist_ok=True)
    binary, rust = build_rust("dpe-source", binary="dpe-screen-preview")
    target = output / "dpe-screen-preview"
    shutil.copy2(binary, target)
    report = {
        "sha256": sha256(target),
        "executable": target.name,
        "license": "GPL-3.0-only",
        "hardwareValidated": False,
        "rust": rust,
        "implementation": "rust",
    }
    write_json(output / "screen-preview.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output)
