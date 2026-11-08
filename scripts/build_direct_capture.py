"""Build the direct descriptor producer without starting capture or opening a GPU."""

import argparse
import shutil
from pathlib import Path

from build_rust_support import build as build_rust
from common import sha256, write_json


def build(output):
    output.mkdir(parents=True, exist_ok=True)
    executable, rust = build_rust("dpe-producer", binary="dpe-direct-source")
    target = output / "dpe-direct-source"
    shutil.copy2(executable, target)
    report = dict(
        schema=1,
        executable=target.name,
        sha256=sha256(target),
        transport="direct-gpu",
        license="GPL-3.0-only",
        hardwareValidated=False,
        rust=rust,
    )
    write_json(output / "direct-source.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    print(build(parser.parse_args().output))
