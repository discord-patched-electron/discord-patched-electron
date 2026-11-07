"""Compile the small camera helper in CI, independently of Electron and makepkg."""

import argparse
import shutil
import subprocess
from pathlib import Path

from build_rust_support import build as build_rust
from common import digest, hash_files, sha256, write_json

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
    export_map = output / "native.exports"
    export_map.write_text("{ global: ioctl; napi_register_module_v1; local: *; };\n")
    environment = {}
    hevc_report = None
    if hevc:
        from build_hevc_support import build as build_hevc

        hevc_report = build_hevc(output / "hevc", compiler)
        environment["DPE_BUNDLED_ENCODER_IMAGE"] = str((output / "hevc/libdpe-hevc.so").resolve())
    rust_archive, rust_report = build_rust(environment=environment)
    source_key = digest(
        {
            "sources": hash_files(
                [
                    "integration/video/capture-abi.h",
                    "integration/video/capture-delivery.h",
                    "integration/video/capture-picker.h",
                    "integration/video/direct-capture.h",
                    "integration/video/direct-capture-gpu.h",
                    "integration/video/capture-profile.h",
                    "integration/video/direct-capture-wire.h",
                    "integration/video/encoder-hevc.h",
                    "integration/video/encoder-abi.h",
                    "integration/video/av1-crop.h",
                    "integration/video/av1-software-crop.h",
                    "integration/video/capture-rate.h",
                    "scripts/build_camera_support.py",
                ]
            ),
            "hevc": hevc_report,
            "rust": rust_report,
        }
    )
    subprocess.run(
        [
            compiler,
            *FLAGS,
            "-Wl,--undefined=napi_register_module_v1",
            "-Wl,--undefined=ioctl",
            "-Wl,--undefined=dpe_native_pointer_reserved",
            "-Wl,--version-script=" + str(export_map),
            "-Wl,--gc-sections",
            str(rust_archive),
            *subprocess.check_output(
                ["pkg-config", "--cflags", "libpipewire-0.3", "libva-drm", "libdrm"], text=True
            ).split(),
            "-o",
            str(output / ASSET),
            "-ldl",
            "-pthread",
            *subprocess.check_output(["pkg-config", "--libs", "libva-drm"], text=True).split(),
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
        "rust": rust_report,
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
