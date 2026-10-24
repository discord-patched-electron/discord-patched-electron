"""Compile the small camera helper in CI, independently of Electron and makepkg."""

import argparse
import subprocess
from pathlib import Path

from common import ROOT, digest, hash_files, sha256, write_json

ASSET = "libdpe-camera-probe.so"
FLAGS = [
    "-std=c11",
    "-O2",
    "-march=x86-64-v3",
    "-mtune=generic",
    "-fPIC",
    "-fvisibility=hidden",
    "-Wall",
    "-Wextra",
    "-Werror",
    "-shared",
    "-Wl,-z,relro,-z,now",
    "-Wl,--build-id=sha1",
]


def build(output: Path, compiler="cc") -> dict:
    output.mkdir(parents=True, exist_ok=True)
    source_key = digest(
        hash_files(["integration/camera/v4l2-probe.c", "scripts/build_camera_support.py"])
    )
    subprocess.run(
        [
            compiler,
            *FLAGS,
            str(ROOT / "integration/camera/v4l2-probe.c"),
            "-o",
            str(output / ASSET),
            "-ldl",
            "-pthread",
        ],
        check=True,
    )
    checksum = sha256(output / ASSET)
    report = {
        "schema": 1,
        "source_key": source_key,
        "sha256": checksum,
        "url": f"https://github.com/discord-patched-electron/discord-patched-electron/releases/download/camera-{checksum}/{ASSET}",
        "compiler": subprocess.check_output([compiler, "--version"], text=True).splitlines()[0],
        "flags": FLAGS,
        "minimum_cpu": "x86-64-v3",
    }
    write_json(output / "camera-support.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compiler", default="cc")
    args = parser.parse_args()
    build(args.output, args.compiler)
