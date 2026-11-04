"""Build bounded GPU monitor thumbnails without capturing a screen."""

import argparse
import subprocess
from pathlib import Path

from build_wayland_capture import protocols
from common import ROOT, sha256, write_json


def build(output, compiler="cc"):
    generated = protocols(output / "protocols")
    flags = subprocess.check_output(
        [
            "pkg-config",
            "--cflags",
            "--libs",
            "wayland-client",
            "gbm",
            "egl",
            "glesv2",
            "libdrm",
            "libcap",
        ],
        text=True,
    ).split()
    target = output / "dpe-screen-preview"
    subprocess.run(
        [
            compiler,
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wl,-z,relro,-z,now",
            "-I" + str(output / "protocols"),
            *(
                str(ROOT / "integration/video" / name)
                for name in ("screen-preview.c", "wayland-capture.c", "kms-client.c", "kms-gpu.c")
            ),
            *map(str, generated),
            *flags,
            "-o",
            str(target),
        ],
        check=True,
    )
    report = {
        "sha256": sha256(target),
        "executable": target.name,
        "license": "GPL-3.0-only",
        "hardwareValidated": False,
    }
    write_json(output / "screen-preview.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output)
