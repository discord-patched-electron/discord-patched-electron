"""Build DPE's KMS producer and bundled privileged server without opening a GPU."""

import argparse
import json
import subprocess
from pathlib import Path

from common import ROOT, read_json, sha256, write_json


def build(output, compiler="cc"):
    output.mkdir(parents=True, exist_ok=True)
    target = output / "dpe-kms-source"
    flags = subprocess.check_output(
        [
            "pkg-config",
            "--cflags",
            "--libs",
            "libpipewire-0.3",
            "gbm",
            "egl",
            "glesv2",
            "libdrm",
            "libcap",
        ],
        text=True,
    ).split()
    subprocess.run(
        [
            compiler,
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wl,-z,relro,-z,now",
            *(
                str(ROOT / "integration/video" / name)
                for name in ("kms-source.c", "kms-client.c", "kms-gpu.c", "vkcapture-relay.c")
            ),
            *flags,
            "-o",
            str(target),
        ],
        check=True,
    )
    pinned = ROOT / "integration/video/vendor/gsr-kms-server.inc"
    provenance = read_json(pinned.with_suffix(".json"))
    if sha256(pinned) != provenance["source_sha256"]:
        raise ValueError("Pinned GSR DRM exporter source changed")
    server = output / "dpe-kms-server"
    drm_flags = subprocess.check_output(
        ["pkg-config", "--cflags", "--libs", "libdrm"], text=True
    ).split()
    subprocess.run(
        [
            compiler,
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fPIE",
            "-pie",
            "-fstack-protector-strong",
            "-D_FORTIFY_SOURCE=3",
            "-ffunction-sections",
            "-fdata-sections",
            "-Wl,--gc-sections,-z,relro,-z,now,-z,noexecstack",
            str(ROOT / "integration/video/kms-server.c"),
            *drm_flags,
            "-o",
            str(server),
        ],
        check=True,
    )
    write_json(
        output / "kms-server.json",
        {
            "sha256": sha256(server),
            "protocol": 6,
            "permissionVersion": 2,
            "executable": server.name,
            "license": "GPL-3.0-only",
            "source": provenance,
        },
    )
    report = {
        "sha256": sha256(target),
        "backend": "kms",
        "protocol": 6,
        "executable": target.name,
        "license": "GPL-3.0-only",
        "installed": False,
        "hardwareValidated": False,
    }
    write_json(output / "kms-source.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output)))
