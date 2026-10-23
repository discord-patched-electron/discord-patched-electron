"""The same preparation path is used by makepkg and CI; never launch Discord."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

from common import read_json, sha256, write_json
from patch_krisp import patch
from prepare_app import prepare


def stage(inputs: dict, directory: Path, asar: str):
    destination = directory / "stage"
    # All destinations are generated inside makepkg's source directory.
    if destination.exists():
        raise ValueError("Staging directory already exists; use makepkg --cleanbuild")
    runtime = inputs["runtime"]
    client = inputs["client"]
    archives = {"runtime.zip": runtime["sha256"]}
    archives.update({name + ".tar.br": source["sha256"] for name, source in client["sources"].items()})
    for name, expected in archives.items():
        if sha256(directory / name) != expected:
            raise ValueError(f"Source checksum mismatch: {name}")
    destination.mkdir()
    (destination / "runtime").mkdir()
    subprocess.run(["unzip", "-q", str(directory / "runtime.zip"), "-d", str(destination / "runtime")], check=True)
    for name in client["sources"]:
        target = destination / "core" if name == "core" else destination / "modules" / name
        target.mkdir(parents=True)
        subprocess.run(["tar", "-xf", str(directory / (name + ".tar.br")),
                        "--use-compress-program=brotli", "--strip-components=1",
                        "-C", str(target), "files/"], check=True)
    core = destination / "core"
    info = read_json(core / "resources/build_info.json")
    if info.get("version") != client["version"]:
        raise ValueError("Core host version differs from locked client")
    prepare(core, client["channel"], inputs["electron_channel"], asar)
    krisp = destination / "modules/discord_krisp"
    report = patch(krisp / "discord_krisp.node")
    (krisp / "KMS/logs").mkdir(parents=True, exist_ok=True)
    write_json(destination / "krisp-patch.json", report)
    for name in ("electron", "chrome-sandbox", "resources.pak", "icudtl.dat", "LICENSE", "LICENSES.chromium.html"):
        if not (destination / "runtime" / name).is_file():
            raise ValueError(f"Incomplete Electron distribution: {name}")
    if not (core / "discord.png").is_file():
        raise ValueError("Discord distribution has no desktop icon")


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
