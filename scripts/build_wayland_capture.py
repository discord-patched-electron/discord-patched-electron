"""Build the direct-output protocol adapter without connecting to a compositor."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from build_rust_support import build as build_rust
from common import ROOT, sha256, write_json


def protocols(output):
    root = ROOT / "integration/video/protocols"
    provenance = json.loads((root / "provenance.json").read_text())
    output.mkdir(parents=True, exist_ok=True)
    sources = []
    for name, entry in provenance["files"].items():
        source = root / name
        if sha256(source) != entry["sha256"]:
            raise ValueError(f"Pinned Wayland protocol changed: {name}")
        stem = source.stem
        for operation, suffix in (
            ("client-header", "-client.h"),
            ("server-header", "-server.h"),
            ("private-code", ".c"),
        ):
            subprocess.run(
                ["wayland-scanner", operation, str(source), str(output / (stem + suffix))],
                check=True,
            )
        sources.append(output / (stem + ".c"))
    return sources


def build(output):
    output.mkdir(parents=True, exist_ok=True)
    archive, rust = build_rust("dpe-wayland")
    target = output / "libdpe-wayland.a"
    shutil.copy2(archive, target)
    report = dict(
        sha256=sha256(target),
        scope="Rust protocol adapters only; no compositor/GPU opened",
        installed=False,
        implementation="rust",
        rust=rust,
    )
    write_json(output / "wayland-capture.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output)))
