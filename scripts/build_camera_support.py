"""Compile the small camera helper in CI, independently of Electron and makepkg."""

import argparse
import shutil
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


def node_headers() -> Path:
    node = Path(shutil.which("node") or "/usr/bin/node").resolve()
    include = node.parent.parent / "include/node"
    if not (include / "node_api.h").is_file():
        raise ValueError("Node.js development headers (node_api.h) are required")
    return include


def build(output: Path, compiler="cc", *, hevc=False) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    include = node_headers()
    extra = []
    hevc_report = None
    if hevc:
        from build_hevc_support import build as build_hevc

        hevc_report = build_hevc(output / "hevc", compiler)
        image = (output / "hevc/libdpe-hevc.so").resolve().as_posix()
        if "\n" in image or "\r" in image:
            raise ValueError("Encoder image path contains a newline")
        image = image.replace("\\", "\\\\").replace('"', '\\"')
        assembly = output / "hevc-image.S"
        assembly.write_text(
            '.section .dpe.hevc,"a",@progbits\n.balign 16\n.global dpe_hevc_image_start\n'
            ".hidden dpe_hevc_image_start\ndpe_hevc_image_start:\n"
            f'.incbin "{image}"\n'
            ".global dpe_hevc_image_end\n.hidden dpe_hevc_image_end\ndpe_hevc_image_end:\n"
            '.section .note.GNU-stack,"",@progbits\n'
        )
        extra.append(str(assembly.resolve()))
    source_key = digest(
        {
            "sources": hash_files(
                [
                    "integration/camera/v4l2-probe.c",
                    "integration/camera/flip.c",
                    "integration/video/capture.c",
                    "integration/video/capture-upstream.c",
                    "integration/video/capture-scale.c",
                    "integration/video/capture-sync.c",
                    "integration/video/capture-picker.c",
                    "integration/video/encoder.c",
                    "integration/video/encoder-hevc.c",
                    "integration/video/encoder-hevc.h",
                    "integration/video/encoder-abi.h",
                    "integration/video/av1-level.h",
                    "integration/video/capture-rate.h",
                    "scripts/build_camera_support.py",
                ]
            ),
            "hevc": hevc_report,
        }
    )
    subprocess.run(
        [
            compiler,
            *FLAGS,
            str(ROOT / "integration/camera/v4l2-probe.c"),
            str(ROOT / "integration/camera/flip.c"),
            str(ROOT / "integration/video/capture.c"),
            str(ROOT / "integration/video/capture-upstream.c"),
            str(ROOT / "integration/video/capture-scale.c"),
            str(ROOT / "integration/video/capture-sync.c"),
            str(ROOT / "integration/video/capture-picker.c"),
            str(ROOT / "integration/video/encoder.c"),
            str(ROOT / "integration/video/encoder-hevc.c"),
            *extra,
            *subprocess.check_output(
                ["pkg-config", "--cflags", "libpipewire-0.3"], text=True
            ).split(),
            "-I" + str(include),
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
        "hevc": hevc_report,
    }
    write_json(output / "camera-support.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compiler", default="cc")
    parser.add_argument("--without-hevc", action="store_true", help="Camera-only developer build")
    args = parser.parse_args()
    build(args.output, args.compiler, hevc=not args.without_hevc)
