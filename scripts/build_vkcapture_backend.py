"""Build the opt-in game backend from pinned source. Never install or launch games."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from build_rust_support import build as build_rust
from build_rust_support import stage_source as stage_rust_source
from common import ROOT, sha256, write_json
from stage_vkcapture import COMMIT, URL, apply_source_patch, stage


def build(output, archive=None):
    output.mkdir(parents=True, exist_ok=True)
    producer, source_rust = build_rust("dpe-source", binary="dpe-vkcapture-source")
    target = output / "dpe-vkcapture-source"
    shutil.copy2(producer, target)
    preview, preview_rust = build_rust(
        "dpe-graphics", features=("preview",), binary="dpe-vkcapture-preview"
    )
    shutil.copy2(preview, output / "dpe-vkcapture-preview")
    if archive is None:
        return {
            "producer": sha256(target),
            "hooksBuilt": False,
            "hardwareValidated": False,
            "source_rust": source_rust,
            "preview_rust": preview_rust,
        }
    source_output = output / "source"
    stage(archive, source_output)
    source = source_output / f"obs-vkcapture-{COMMIT}"
    patch = ROOT / "patches/capture/vkcapture-dpe-handoff.patch"
    apply_source_patch(source, patch)
    for name in (
        "vkcapture-wire.h",
        "vkcapture-hook.h",
        "vkcapture-gl-sync.h",
        "vkcapture-rust.cmake",
    ):
        shutil.copy2(ROOT / "integration/video" / name, source / "src" / name)
    rust_archive, rust = build_rust("dpe-game")
    stage_rust_source(source / "dpe-rust")
    with (source / "CMakeLists.txt").open("a") as cmake:
        cmake.write('\ninclude("${CMAKE_CURRENT_SOURCE_DIR}/src/vkcapture-rust.cmake")\n')
    subprocess.run(
        [
            "cmake",
            "-S",
            str(source),
            "-B",
            str(output / "build"),
            "-G",
            "Ninja",
            "-DBUILD_PLUGIN=OFF",
            "-DCMAKE_BUILD_TYPE=Release",
            "-DDPE_GAME_ARCHIVE=" + str(rust_archive),
        ],
        check=True,
    )
    subprocess.run(["cmake", "--build", str(output / "build"), "--parallel", "1"], check=True)
    for name in ("libobs_glcapture.so", "libVkLayer_obs_vkcapture.so"):
        shutil.copy2(output / "build" / name, output / name)
    layer = json.loads((output / "build/obs_vkcapture_64.json").read_text())
    layer["layer"]["name"] = "VK_LAYER_DPE_gamecapture_64"
    layer["layer"]["library_path"] = "../libVkLayer_obs_vkcapture.so"
    layer["layer"].pop("enable_environment", None)
    layer["layer"].pop("disable_environment", None)
    (output / "layers").mkdir(exist_ok=True)
    write_json(output / "layers/dpe_vkcapture.json", layer)
    for name in ("gamecapture-launch.py", "vkcapture-broker.py"):
        shutil.copy2(ROOT / "integration/video" / name, output / name)
        (output / name).chmod(0o755)
    # Corresponding patched hook source and both license notices ship together.
    # The broker remains MIT; source and preview consumers use GPL-3.0-only Rust
    # graphics. Combined binaries ship that license and corresponding source.
    license_files = [
        p for p in source.iterdir() if p.name.lower().startswith(("license", "copying"))
    ]
    for file in license_files:
        shutil.copy2(file, output / ("HOOK-" + file.name))
    shutil.copy2(ROOT / "LICENSE", output / "DPE-LICENSE")
    shutil.copy2(
        ROOT / "integration/video/protocols/GSR-GPL-3.0.txt", output / "GRAPHICS-GPL-3.0.txt"
    )
    report = {
        "protocol": 1,
        "rust": rust,
        "source_rust": source_rust,
        "preview_rust": preview_rust,
        "upstreamCommit": COMMIT,
        "upstreamUrl": URL,
        "hookLicense": "GPL-2.0-or-later",
        "correspondingSource": str(source.relative_to(output)),
        "hardwareValidated": False,
        "architecture": "x86_64",
        "files": {
            name: sha256(output / name)
            for name in (
                "dpe-vkcapture-source",
                "dpe-vkcapture-preview",
                "libobs_glcapture.so",
                "libVkLayer_obs_vkcapture.so",
            )
        },
    }
    write_json(output / "backend.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(build(args.output.resolve(), args.archive.resolve() if args.archive else None))
    )
