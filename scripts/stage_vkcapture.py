"""Prepare the opt-in vkcapture producer pacing experiment; never install/load it.

The upstream archive is pinned. --build compiles only the Vulkan and OpenGL
hooks, with one build job and no graphics device probe or application launch.
"""

import argparse
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from common import ROOT, sha256, write_json

COMMIT = "8a2c43295fae2ede8ffd382a1581281ceb6153c0"
ARCHIVE_SHA256 = "7eb948216cef18993b8d7abcd3087a45175e2cdb9255e48e1c6d40e762e3bbd8"
URL = f"https://codeload.github.com/nowrep/obs-vkcapture/tar.gz/{COMMIT}"


def apply_source_patch(directory, patch):
    # Extracted upstream trees have no index. A parent checkout can otherwise
    # make git apply silently skip their paths, or resolve a host-only worktree.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX"}
    }
    environment["GIT_CEILING_DIRECTORIES"] = str(directory.resolve().parent)
    for flags in (["--check"], []):
        subprocess.run(
            ["git", "apply", *flags, str(patch.resolve())],
            cwd=directory,
            env=environment,
            check=True,
        )


def check_policy(directory, output):
    binary = output / "vkcapture-policy-check"
    subprocess.run(
        [
            "cc",
            "-std=gnu11",
            "-D_GNU_SOURCE",
            "-O1",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fsanitize=address,undefined",
            "-fno-omit-frame-pointer",
            "-Dclock_gettime=fixture_clock_gettime",
            "-I" + str(directory / "src"),
            str(directory / "src/capture.c"),
            str(ROOT / "tests/screenshare/vkcapture-policy-check.c"),
            "-o",
            str(binary),
        ],
        check=True,
    )
    subprocess.run([str(binary)], check=True, timeout=10)


def stage(archive, output, *, build=False):
    if output.exists():
        raise ValueError("Choose a fresh candidate directory")
    if sha256(archive) != ARCHIVE_SHA256:
        raise ValueError("vkcapture archive differs from the reviewed revision")
    output.mkdir(parents=True)
    with tarfile.open(archive) as source:
        prefix = f"obs-vkcapture-{COMMIT}"
        if any(Path(member.name).parts[0] != prefix for member in source):
            raise ValueError("Unexpected vkcapture archive layout")
        source.extractall(output, filter="data")
    directory = output / prefix
    patch = ROOT / "patches/capture/vkcapture-copy-rate.patch"
    apply_source_patch(directory, patch)
    shutil.copy2(ROOT / "integration/video/capture-rate.h", directory / "src/capture-rate.h")
    shutil.copy2(ROOT / "LICENSE", directory / "DPE-RATE-LICENSE")
    report = dict(
        upstream_commit=COMMIT,
        archive_url=URL,
        archive_sha256=ARCHIVE_SHA256,
        patch_sha256=sha256(patch),
        limiter_sha256=sha256(directory / "src/capture-rate.h"),
        license="GPL-2.0-or-later; MIT limiter notice retained separately",
        installed=False,
        launched=False,
        hardware_validated=False,
        setting="DPE_VKCAPTURE_MAX_FPS=1..240; unset/invalid leaves upstream unlimited capture",
    )
    if build:
        check_policy(directory, output)
        report["patched_policy_test"] = "passed; synthetic clock, no socket/GPU/present calls"
        subprocess.run(
            [
                "cmake",
                "-S",
                str(directory),
                "-B",
                str(output / "build"),
                "-G",
                "Ninja",
                "-DBUILD_PLUGIN=OFF",
                "-DCMAKE_BUILD_TYPE=Release",
            ],
            check=True,
        )
        subprocess.run(["cmake", "--build", str(output / "build"), "--parallel", "1"], check=True)
        report["binaries"] = {
            name: sha256(output / "build" / name)
            for name in ("libVkLayer_obs_vkcapture.so", "libobs_glcapture.so")
        }
    write_json(output / "vkcapture-candidate.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help=URL)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    print(json.dumps(stage(args.archive.resolve(), args.output.resolve(), build=args.build)))
