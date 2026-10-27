"""The same preparation path is used by makepkg and CI; never launch Discord."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from application_payload import bootstrap, bundle
from common import read_json, sha256
from stage_updater import configure_runtime, validate_distribution
from stage_updater import stage as stage_updater


def stage(inputs: dict, directory: Path, asar: str):
    destination = directory / "application"
    # All destinations are generated inside makepkg's source directory.
    if destination.exists():
        raise ValueError("Staging directory already exists; use makepkg --cleanbuild")
    runtime = inputs["runtime"]
    client = inputs["client"]
    archives = {"libdpe-camera-probe.so": inputs["camera_support"]["sha256"]}
    for name, expected in archives.items():
        if sha256(directory / name) != expected:
            raise ValueError(f"Source checksum mismatch: {name}")
    destination.mkdir()
    stage_updater(
        destination,
        client["channel"],
        client["version"],
        asar,
        directory / "openasar.tar.gz",
        client=client,
        python_archives=directory,
        cdn_archive=directory / "openasar-cdn.tar.gz",
    )
    configure_runtime(
        destination,
        client["channel"],
        inputs["electron_channel"],
        runtime["version"],
        runtime["runtime_key"],
        runtime["sha256"],
        runtime["url"],
    )
    shutil.copy2(directory / "libdpe-camera-probe.so", destination)
    launcher = (
        (directory / "discord-launcher.sh")
        .read_text()
        .replace(
            "root=/usr/lib/" + inputs["package"],
            'root=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")',
        )
    )
    (destination / "launch.sh").write_text(launcher)
    (destination / "launch.sh").chmod(0o755)
    shutil.copy2(directory / "discord.desktop", destination / "discord.desktop")
    validate_distribution(destination, allow_runtime=False)
    # This developer recipe produces two artifacts, just like release packaging.
    # Only stage/ is installed; application.zip can seed an unpublished fixture.
    application = bundle(destination, directory / "application.zip", "20260101.000000", 1767225600)
    bootstrap(
        destination,
        directory / "stage",
        application,
        "local-" + application["archive_sha256"],
        client["channel"],
        inputs["electron_channel"],
    )


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
