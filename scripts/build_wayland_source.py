"""Build the Rust direct-output GPU/PipeWire producer without starting capture."""

import argparse
import json
import shutil
from pathlib import Path

from build_rust_support import build as build_rust
from common import sha256, write_json


def build(output):
    output.mkdir(parents=True, exist_ok=True)
    binary, rust = build_rust("dpe-source", binary="dpe-wayland-source")
    target = output / "dpe-wayland-source"
    shutil.copy2(binary, target)
    report = dict(
        sha256=sha256(target),
        scope="direct output GPU producer; build does not open a compositor or GPU",
        backend="wayland-direct",
        executable=target.name,
        installed=False,
        implementation="rust",
        license="GPL-3.0-only",
        hardwareValidated=False,
        rust=rust,
    )
    write_json(output / "wayland-source.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output)))
