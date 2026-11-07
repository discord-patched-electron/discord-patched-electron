#!/usr/bin/env python3
"""Rebuild the bundled game consumers from their supplied corresponding source."""

import argparse
import shutil
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    rust = source / "rust"
    if not (rust / "Cargo.toml").is_file():
        rust = source.parents[1]  # original repository checkout
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    target = output / "rust-target"
    for package, binary, features in (
        ("dpe-source", "dpe-vkcapture-source", ()),
        ("dpe-graphics", "dpe-vkcapture-preview", ("preview",)),
    ):
        subprocess.run(
            [
                "cargo",
                "build",
                "--locked",
                "--release",
                "--target-dir",
                str(target),
                "-p",
                package,
                "--bin",
                binary,
                *(["--features", ",".join(features)] if features else []),
            ],
            cwd=rust,
            check=True,
        )
        shutil.copy2(target / "release" / binary, output / binary)


if __name__ == "__main__":
    main()
