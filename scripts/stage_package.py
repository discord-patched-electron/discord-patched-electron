"""The same preparation path is used by makepkg and CI; never launch Discord."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from common import read_json, sha256
from stage_updater import stage as stage_updater
from stage_updater import validate_distribution


def stage(inputs: dict, directory: Path, asar: str):
    destination = directory / "stage"
    # All destinations are generated inside makepkg's source directory.
    if destination.exists():
        raise ValueError("Staging directory already exists; use makepkg --cleanbuild")
    runtime = inputs["runtime"]
    client = inputs["client"]
    archives = {"runtime.zip": runtime["sha256"]}
    archives["libdpe-camera-probe.so"] = inputs["camera_support"]["sha256"]
    for name, expected in archives.items():
        if sha256(directory / name) != expected:
            raise ValueError(f"Source checksum mismatch: {name}")
    destination.mkdir()
    (destination / "runtime").mkdir()
    subprocess.run(
        ["unzip", "-q", str(directory / "runtime.zip"), "-d", str(destination / "runtime")],
        check=True,
    )
    stage_updater(
        destination, client["channel"], client["version"], asar, directory / "openasar.tar.gz"
    )
    resources = destination / "runtime/resources"
    if resources.exists():
        import shutil

        shutil.rmtree(resources)
    resources.symlink_to("../resources")
    validate_distribution(destination)
    for name in (
        "electron",
        "chrome-sandbox",
        "resources.pak",
        "icudtl.dat",
        "LICENSE",
        "LICENSES.chromium.html",
    ):
        if not (destination / "runtime" / name).is_file():
            raise ValueError(f"Incomplete Electron distribution: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--asar", required=True)
    args = parser.parse_args()
    # makepkg symlinks local sources into src/. Resolving the JSON symlink would
    # stage beside the original recipe, outside the directory package() uses.
    stage(read_json(args.inputs), args.inputs.absolute().parent, args.asar)


if __name__ == "__main__":
    main()
