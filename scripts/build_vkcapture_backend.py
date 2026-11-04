"""Build the opt-in game backend from pinned source. Never install or launch games."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from common import ROOT, sha256, write_json
from stage_vkcapture import COMMIT, URL, stage


def build(output, archive=None):
    output.mkdir(parents=True, exist_ok=True)
    target = output / "dpe-vkcapture-source"
    flags = subprocess.check_output(
        ["pkg-config", "--cflags", "--libs", "libpipewire-0.3", "gbm", "egl", "glesv2", "libdrm"],
        text=True,
    ).split()
    subprocess.run(
        [
            "cc",
            "-std=gnu11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wl,-z,relro,-z,now",
            str(ROOT / "integration/video/vkcapture-source.c"),
            str(ROOT / "integration/video/vkcapture-gpu.c"),
            str(ROOT / "integration/video/vkcapture-relay.c"),
            *flags,
            "-o",
            str(target),
        ],
        check=True,
    )
    subprocess.run(
        [
            "cc",
            "-std=gnu11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wl,-z,relro,-z,now",
            str(ROOT / "integration/video/vkcapture-preview.c"),
            str(ROOT / "integration/video/vkcapture-gpu.c"),
            *flags,
            "-o",
            str(output / "dpe-vkcapture-preview"),
        ],
        check=True,
    )
    if archive is None:
        return {"producer": sha256(target), "hooksBuilt": False, "hardwareValidated": False}
    source_output = output / "source"
    stage(archive, source_output)
    source = source_output / f"obs-vkcapture-{COMMIT}"
    patch = ROOT / "patches/capture/vkcapture-dpe-handoff.patch"
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=source, check=True)
    subprocess.run(["git", "apply", str(patch)], cwd=source, check=True)
    for name in (
        "vkcapture-wire.h",
        "vkcapture-hook.h",
        "vkcapture-hook.c",
        "vkcapture-gl-sync.h",
        "vkcapture-gl-sync.c",
    ):
        shutil.copy2(ROOT / "integration/video" / name, source / "src" / name)
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
    # Consumer/broker source remains MIT and is supplied in the DPE application.
    license_files = [
        p for p in source.iterdir() if p.name.lower().startswith(("license", "copying"))
    ]
    for file in license_files:
        shutil.copy2(file, output / ("HOOK-" + file.name))
    shutil.copy2(ROOT / "LICENSE", output / "DPE-LICENSE")
    report = {
        "protocol": 1,
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
